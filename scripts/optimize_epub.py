"""Reduce existing EPUB image payloads using the pipeline's reader-size limits.

Requires rsvg-convert (librsvg) for self-contained, static SVG illustrations.
Text, identifiers, hyperlinks, metadata and spine order are checked before write.
Photo selection is explicit; filenames never determine image type or cover status.
"""

import argparse
import copy
import fnmatch
import io
import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from lxml import etree
from PIL import Image, ImageCms, ImageOps

from common import digest
from figure_cleanup import (
    EBOOK_PHOTO_PPI,
    EBOOK_TARGET_PPI,
    EBOOK_VIEWPORT,
    ebook_size,
    export_color,
    jpeg_export,
)

OPF = "{http://www.idpf.org/2007/opf}"
HTML = "{http://www.w3.org/1999/xhtml}"
SVG = "{http://www.w3.org/2000/svg}"
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
CSS_URL = re.compile(
    r"url\(\s*(['\"]?)(.*?)\1\s*\)|@import\s+['\"]([^'\"]+)['\"]", re.S | re.I
)
RASTER_TYPES = {"image/jpeg", "image/png"}


@dataclass(frozen=True)
class Policy:
    paperwhite: bool = False
    photos: tuple[str, ...] = ()
    keep_svg: bool = False
    fix_markup: bool = False
    jpeg_quality: int = 85


def parse_xml(data):
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    root = etree.fromstring(data, parser)
    if any(isinstance(node, etree._Entity) for node in root.iter()):
        raise ValueError("XML entity references are not supported")
    return root


def serialize(root, original):
    return etree.tostring(
        root.getroottree(),
        encoding="utf-8",
        xml_declaration=original.lstrip().startswith(b"<?xml"),
    )


def local_path(document, href):
    """Resolve a local URI to a ZIP member, including percent-encoded filenames."""
    url = urlsplit(href)
    if url.scheme or url.netloc:
        return None
    if not url.path:
        return document
    return posixpath.normpath(
        posixpath.join(posixpath.dirname(document), unquote(url.path))
    )


def relative_uri(document, target):
    return quote(posixpath.relpath(target, posixpath.dirname(document)), safe="/.-_~")


def xml_file(name, media):
    return name.lower().endswith(
        (".xml", ".opf", ".ncx", ".xhtml", ".html", ".svg", ".smil")
    ) or media.endswith("+xml")


def references(name, data, media=""):
    """Return static dependencies; None means pruning cannot be proved safe."""
    result = []

    def css(text):
        # CSS escapes need a full CSS parser. Retain media rather than guessing.
        if "\\" in text:
            return False
        for match in CSS_URL.finditer(text):
            target = local_path(name, match[2] if match[2] is not None else match[3])
            if target:
                result.append(target)
        return True

    if name.lower().endswith(".css") or media == "text/css":
        return result if css(data.decode("utf-8-sig")) else None
    if not xml_file(name, media):
        return result
    root = parse_xml(data)
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        tag = etree.QName(node).localname
        if XML_BASE in node.attrib or tag == "script":
            return None
        # Manifest membership declares availability, not actual use.
        if (
            node.tag == OPF + "manifest"
            or node.getparent() is not None
            and node.getparent().tag == OPF + "manifest"
        ):
            continue
        for key, value in node.attrib.items():
            attr = etree.QName(key).localname
            if attr in ("href", "src", "data", "poster", "background", "URI"):
                target = local_path(name, value)
                if target:
                    result.append(target)
            elif attr == "srcset":
                if "data:" in value:
                    return None
                for candidate in value.split(","):
                    if candidate.strip():
                        target = local_path(name, candidate.split()[0])
                        if target:
                            result.append(target)
            elif attr == "style" and not css(value):
                return None
        if tag == "style" and not css("".join(node.itertext())):
            return None
    return result


def content_signature(root):
    body = root.find(HTML + "body")
    if body is None:
        body = root
    return (
        re.sub(r"\s+", " ", "".join(body.itertext())).strip(),
        [node.get("id") for node in root.iter() if node.get("id")],
        [
            (dict(node.attrib), "".join(node.itertext()))
            for node in root.iter(HTML + "a")
        ],
        len(list(root.iter(HTML + "img"))),
    )


@lru_cache
def jpeg_tables(quality):
    encoded = jpeg_export(Image.new("RGB", (1, 1)), quality)
    with Image.open(io.BytesIO(encoded)) as image:
        return image.quantization


def encode_image(data, media, policy, *, cover=False, photo=False):
    with Image.open(io.BytesIO(data)) as source:
        if getattr(source, "n_frames", 1) != 1:
            return data, {"skipped": "animated image"}
        rotated = source.getexif().get(274, 1) != 1
        image = ImageOps.exif_transpose(source)
        original_size = image.size
        # Normalize a tagged image to sRGB before discarding publishing metadata.
        # Preserve transparency independently of color conversion.
        alpha = (
            image.convert("RGBA").getchannel("A")
            if "A" in image.getbands() or "transparency" in image.info
            else None
        )
        if image.info.get("icc_profile"):
            try:
                image = ImageCms.profileToProfile(
                    image.convert("RGB") if image.mode == "P" else image,
                    ImageCms.ImageCmsProfile(io.BytesIO(image.info["icc_profile"])),
                    ImageCms.createProfile("sRGB"),
                    outputMode="RGB",
                )
            except ImageCms.PyCMSError as exc:
                raise ValueError(f"Cannot normalize image ICC profile: {exc}") from exc
        image, color = export_color(
            image, "grayscale" if policy.paperwhite and not cover else "auto"
        )
        if alpha is not None:
            image.putalpha(alpha)
        image, sizing = ebook_size(
            image, EBOOK_PHOTO_PPI if photo and not cover else EBOOK_TARGET_PPI
        )
        evidence = dict(
            original=list(original_size),
            pixels=list(image.size),
            color=color,
            sizing=sizing,
            cover=cover,
            photo=photo and not cover,
        )
        if (
            media == "image/jpeg"
            and not rotated
            and not sizing["resized"]
            and source.mode in {"L", "RGB"}
            and not (policy.paperwhite and not cover and source.mode != "L")
        ):
            tables = jpeg_tables(policy.jpeg_quality)
            if source.quantization and all(
                key in tables
                and all(old >= target for old, target in zip(values, tables[key]))
                for key, values in source.quantization.items()
            ):
                # Re-encoding an already compact JPEG compounds loss on every
                # invocation. Coarser/equal quantization needs no new encode.
                return data, evidence | {
                    "kept_original": True,
                    "reason": "already at JPEG quality target",
                }
        image.info.clear()
        stream = io.BytesIO()
        if media == "image/jpeg":
            encoded = jpeg_export(image, policy.jpeg_quality)
        else:
            options = {}
            if policy.paperwhite and not cover and image.mode == "L":
                image = image.point(
                    [round(value / 17) for value in range(256)]
                ).convert("P")
                image.putpalette([value for i in range(16) for value in [i * 17] * 3])
                options["bits"] = 4
            image.save(stream, format="PNG", optimize=True, compress_level=9, **options)
            encoded = stream.getvalue()
        # Do not inflate an already bounded asset unless the requested color
        # policy changes it. Oversized sources must still satisfy the viewport.
        if (
            len(encoded) >= len(data)
            and not sizing["resized"]
            and not rotated
            and not (policy.paperwhite and not cover)
        ):
            return data, evidence | {"kept_original": True}
        return encoded, evidence


def render_svg(data, policy):
    root = parse_xml(data)
    if root.getroottree().xpath("//processing-instruction('xml-stylesheet')"):
        raise ValueError("SVG depends on an external stylesheet")
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        if etree.QName(node).localname in {
            "script",
            "animate",
            "animateMotion",
            "animateTransform",
            "set",
            "foreignObject",
        }:
            raise ValueError("dynamic or foreign SVG content")
        if XML_BASE in node.attrib:
            raise ValueError("SVG xml:base")
        for key, value in node.attrib.items():
            if key.lower().startswith("on"):
                raise ValueError("dynamic SVG event handler")
            if (
                etree.QName(key).localname == "href"
                and value
                and not value.startswith(("#", "data:"))
            ):
                raise ValueError("SVG depends on external resources")
        text = node.get("style", "") + " " + (node.text or "")
        if (
            "\\" in text
            or "@import" in text
            or any(
                not match[2].startswith(("#", "data:"))
                for match in CSS_URL.finditer(text)
                if match[2] is not None
            )
        ):
            raise ValueError("SVG depends on external styles or resources")
    converter = shutil.which("rsvg-convert")
    if converter is None:
        raise ValueError(
            "rsvg-convert is unavailable; install librsvg or use --keep-svg"
        )
    command = [
        converter,
        "--unlimited",
        "--keep-aspect-ratio",
        "--width",
        str(EBOOK_VIEWPORT[0]),
        "--height",
        str(EBOOK_VIEWPORT[1]),
    ]
    if policy.paperwhite:
        command += ["--background-color", "white"]
    result = subprocess.run(
        command, input=data, capture_output=True, check=True, timeout=60
    )
    return encode_image(result.stdout, "image/png", policy)[0]


def replacement_image(node, svg_root):
    """Only collapse simple image fallbacks; retain complex/interactive objects."""
    if node.tag == HTML + "img":
        image = copy.deepcopy(node)
    else:
        children = list(node)
        if (
            len(children) != 1
            or children[0].tag != HTML + "img"
            or (node.text or "").strip()
            or (children[0].tail or "").strip()
        ):
            return None
        image = copy.deepcopy(children[0])
        if node.get("id") and image.get("id"):
            return None
        for key, value in node.attrib.items():
            if key in ("data", "type"):
                continue
            if key not in {
                "id",
                "class",
                "style",
                "title",
                "width",
                "height",
                "role",
                "lang",
                "dir",
                "tabindex",
            } and not key.startswith("aria-"):
                return None
            if key in image.attrib and image.get(key) != value:
                return None
            image.set(key, value)
    if (
        any(attr in image.attrib for attr in ("usemap", "ismap", "srcset"))
        or node.getparent().tag == HTML + "picture"
    ):
        return None
    width = svg_root.get("width", "")
    if (
        "width" not in image.attrib
        and re.fullmatch(r"[0-9.]+(?:px)?", width)
        and not re.search(r"(?:^|;)\s*width\s*:", image.get("style", ""))
    ):
        image.set("width", str(max(1, round(float(width.removesuffix("px"))))))
    image.set(
        "style", image.get("style", "").rstrip("; ") + ";max-width:100%;height:auto;"
    )
    image.tail = node.tail
    return image


def repair_markup(root):
    repairs = []
    for node in root.iter():
        if (
            node.tag == HTML + "hgroup"
            and sum(
                child.tag in {HTML + f"h{i}" for i in range(1, 7)} for child in node
            )
            > 1
        ):
            node.tag = HTML + "div"
            repairs.append("multi-heading-hgroup-to-div")
        if node.tag == HTML + "nav" and node.get("aria-labelledby"):
            heading_ids = {
                child.get("id")
                for child in node.iter()
                if child.tag in {HTML + f"h{i}" for i in range(1, 7)}
            }
            if set(node.get("aria-labelledby").split()) <= heading_ids:
                del node.attrib["aria-labelledby"]
                repairs.append("redundant-nav-labelledby")
    return repairs


def unused_media(files, types, package_path, package, covers, protected=()):
    dependencies = {
        name: [] if name in protected else references(name, data, types.get(name, ""))
        for name, data in files.items()
    }
    # Opaque scripts, CSS escapes or inherited bases can hide dependencies.
    if any(value is None for value in dependencies.values()) or any(
        media in {"text/javascript", "application/javascript"}
        for media in types.values()
    ):
        return set(), "unresolved dynamic, escaped, or base-relative references"
    manifest = package.find(OPF + "manifest")
    by_id = {
        item.get("id"): local_path(package_path, item.get("href"))
        for item in manifest.findall(OPF + "item")
    }
    for item in manifest.findall(OPF + "item"):
        if item.get("fallback") in by_id:
            dependencies[local_path(package_path, item.get("href"))].append(
                by_id[item.get("fallback")]
            )
    media = {name for name, kind in types.items() if kind.startswith("image/")}
    reached = set(files) - media | covers | set(protected)
    # ID-valued package references (spine, refinements, bindings and custom
    # metadata) are dependencies too, even when no XHTML href uses the asset.
    for node in package.iter():
        for key, value in node.attrib.items():
            if key in {"id", "fallback", "href"}:
                continue
            reached.update(
                by_id[token.lstrip("#")]
                for token in value.split()
                if token.lstrip("#") in by_id
            )
    pending = list(reached)
    while pending:
        for target in dependencies.get(pending.pop(), []):
            if target in files and target not in reached:
                reached.add(target)
                pending.append(target)
    return media - reached, None


def optimize(source, output, policy=Policy(), *, force=False):
    source, output = Path(source), Path(output)
    if (
        source.resolve() == output.resolve()
        or output.exists()
        and os.path.samefile(source, output)
    ):
        raise ValueError("Input and output must be different files")
    if output.exists() and not force:
        raise ValueError(f"Output exists: {output}; use --force to replace it")
    if not 1 <= policy.jpeg_quality <= 100:
        raise ValueError("JPEG quality must be between 1 and 100")
    source_data = source.read_bytes()
    with zipfile.ZipFile(io.BytesIO(source_data)) as archive:
        infos = {item.filename: item for item in archive.infolist()}
        if len(infos) != len(archive.infolist()):
            raise ValueError("Duplicate ZIP member names are not supported")
        files = {name: archive.read(name) for name in infos}
    if "META-INF/signatures.xml" in files:
        raise ValueError(
            "Signed EPUBs cannot be modified without invalidating their signature"
        )
    container = parse_xml(files["META-INF/container.xml"])
    roots = container.findall(
        ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
    )
    if len(roots) != 1:
        raise ValueError("Expected one EPUB package in container.xml")
    package_path = roots[0].get("full-path")
    package = parse_xml(files[package_path])
    manifest = package.find(OPF + "manifest")
    items = {
        local_path(package_path, item.get("href")): item
        for item in manifest.findall(OPF + "item")
    }
    types = {name: item.get("media-type", "") for name, item in items.items()}
    protected = set()
    if "META-INF/encryption.xml" in files:
        encrypted = parse_xml(files["META-INF/encryption.xml"])
        protected = {
            unquote(node.get("URI")) for node in encrypted.iter() if node.get("URI")
        }
    covers = {
        name
        for name, item in items.items()
        if "cover-image" in item.get("properties", "").split()
    }
    for meta in package.findall(OPF + "metadata/" + OPF + "meta"):
        if meta.get("name") == "cover":
            covers.update(
                name
                for name, item in items.items()
                if item.get("id") == meta.get("content")
            )
    documents = {
        name: parse_xml(files[name])
        for name, media in types.items()
        if media == "application/xhtml+xml"
    }
    for reference in package.findall(OPF + "guide/" + OPF + "reference"):
        if reference.get("type") == "cover":
            target = local_path(package_path, reference.get("href", ""))
            if types.get(target, "").startswith("image/"):
                covers.add(target)
            elif target in documents:
                covers.update(
                    local_path(target, image.get("src", ""))
                    for image in documents[target].iter(HTML + "img")
                )
    for name, root in documents.items():
        for image in root.iter(HTML + "img"):
            if "usemap" in image.attrib or "ismap" in image.attrib:
                protected.add(local_path(name, image.get("src", "")))
            if (
                image.get("role") == "doc-cover"
                or "cover"
                in image.get("{http://www.idpf.org/2007/ops}type", "").split()
            ):
                covers.add(local_path(name, image.get("src", "")))
    signatures = {name: content_signature(root) for name, root in documents.items()}
    stable = {
        tag: etree.tostring(package.find(OPF + tag))
        for tag in ("metadata", "spine")
        if package.find(OPF + tag) is not None
    }
    report = dict(
        source=str(source),
        source_sha256=digest(source_data),
        source_bytes=len(source_data),
        viewport=list(EBOOK_VIEWPORT),
        photo_viewport=[v // 2 for v in EBOOK_VIEWPORT],
        policy=dict(
            paperwhite=policy.paperwhite,
            photos=list(policy.photos),
            jpeg_quality=policy.jpeg_quality,
        ),
        images=[],
        skipped=[
            dict(
                path=name, reason="encrypted or mapped image; retain source coordinates"
            )
            for name in sorted(protected)
            if types.get(name, "").startswith("image/")
        ],
        markup_repairs=[],
    )
    changed_documents = set()
    converted = {}
    initial_refs = Counter()
    unsafe_bases = any(
        any(
            XML_BASE in node.attrib or node.tag == HTML + "script"
            for node in root.iter()
        )
        for root in documents.values()
    ) or any(
        media in {"text/javascript", "application/javascript"}
        for media in types.values()
    )
    css_sources = [
        files[name].decode("utf-8-sig")
        for name, kind in types.items()
        if kind == "text/css"
    ]
    css_sources += [
        "".join(node.itertext())
        for root in documents.values()
        for node in root.iter(HTML + "style")
    ]
    # Tag/URI-specific author rules cannot be transferred faithfully to a new
    # img element. Keep SVG objects when their replacement could lose styling.
    for css in css_sources:
        selectors = re.findall(r"([^{}]+)\{", re.sub(r"/\*.*?\*/", "", css, flags=re.S))
        if any(
            re.search(r"\bobject\b|\[[^\]]*\b(?:src|data|type)\b", selector, re.I)
            for selector in selectors
        ):
            unsafe_bases = True
    if unsafe_bases and not policy.keep_svg:
        report["skipped"].append(
            dict(
                path="XHTML SVG replacements",
                reason="scripts, XML bases or tag/URI-specific author styles",
            )
        )
    for name, data in files.items():
        if name not in protected:
            initial_refs.update(references(name, data, types.get(name, "")) or [])
    for name, root in documents.items():
        if not policy.keep_svg and not unsafe_bases:
            for node in list(root.iter()):
                attr = (
                    "data"
                    if node.tag == HTML + "object"
                    else "src" if node.tag == HTML + "img" else None
                )
                if (
                    attr is None
                    or not node.get(attr)
                    or urlsplit(node.get(attr)).fragment
                ):
                    continue
                svg = local_path(name, node.get(attr))
                if (
                    types.get(svg) != "image/svg+xml"
                    or svg in covers
                    or svg in protected
                ):
                    continue
                svg_root = parse_xml(files[svg])
                image = replacement_image(node, svg_root)
                if image is None:
                    report["skipped"].append(
                        dict(
                            path=svg,
                            reason="complex object fallback or conflicting attributes",
                        )
                    )
                    continue
                if svg not in converted:
                    try:
                        png = render_svg(files[svg], policy)
                    except (ValueError, subprocess.SubprocessError) as exc:
                        report["skipped"].append(dict(path=svg, reason=str(exc)))
                        continue
                    old_cost = len(zlib.compress(files[svg], 9))
                    fallback = node.find(HTML + "img")
                    if fallback is not None:
                        path = local_path(name, fallback.get("src", ""))
                        if initial_refs[path] == 1 and path in files:
                            old_cost += len(zlib.compress(files[path], 9))
                    if len(zlib.compress(png, 9)) >= old_cost:
                        report["skipped"].append(
                            dict(path=svg, reason="raster rendition is not smaller")
                        )
                        continue
                    stem = posixpath.splitext(svg)[0] + ".optimized"
                    target = stem + ".png"
                    number = 1
                    while target in files:
                        target = f"{stem}-{number}.png"
                        number += 1
                    files[target] = png
                    types[target] = "image/png"
                    converted[svg] = target
                    ids = {item.get("id") for item in manifest.findall(OPF + "item")}
                    ident = "optimized-image-" + str(len(converted))
                    while ident in ids:
                        ident += "-new"
                    etree.SubElement(
                        manifest,
                        OPF + "item",
                        id=ident,
                        href=relative_uri(package_path, target),
                        **{"media-type": "image/png"},
                    )
                    report["images"].append(
                        dict(
                            source=svg,
                            source_bytes=len(files[svg]),
                            output=target,
                            bytes=len(png),
                            action="svg-to-png",
                        )
                    )
                image.set("src", relative_uri(name, converted[svg]))
                if node.tag == HTML + "img":
                    image.tail = node.tail
                node.getparent().replace(node, image)
                changed_documents.add(name)
        if policy.fix_markup:
            repairs = repair_markup(root)
            if repairs:
                changed_documents.add(name)
                report["markup_repairs"].append(dict(path=name, rules=repairs))
    for name in changed_documents:
        files[name] = serialize(documents[name], files[name])
    for name, media in list(types.items()):
        if (
            media not in RASTER_TYPES
            or name not in files
            or name in converted.values()
            or name in protected
        ):
            continue
        photo = any(fnmatch.fnmatchcase(name, pattern) for pattern in policy.photos)
        source_bytes = len(files[name])
        encoded, evidence = encode_image(
            files[name], media, policy, cover=name in covers, photo=photo
        )
        files[name] = encoded
        report["images"].append(
            dict(
                source=name,
                source_bytes=source_bytes,
                output=name,
                bytes=len(encoded),
                **evidence,
            )
        )
    files[package_path] = serialize(package, files[package_path])
    unused, pruning_note = unused_media(
        files, types, package_path, package, covers, protected
    )
    for item in manifest.findall(OPF + "item"):
        if local_path(package_path, item.get("href")) in unused:
            manifest.remove(item)
    for name in unused:
        files.pop(name)
    # Update SVG content flags only after checking remaining inline/referenced SVG.
    for name, root in documents.items():
        item = items[name]
        properties = item.get("properties", "").split()
        refs = references(name, files[name], types[name])
        if (
            "svg" in properties
            and refs is not None
            and not any(node.tag == SVG + "svg" for node in root.iter())
            and not any(types.get(ref) == "image/svg+xml" for ref in refs)
        ):
            properties.remove("svg")
            if properties:
                item.set("properties", " ".join(properties))
            else:
                item.attrib.pop("properties", None)
    for tag, before in stable.items():
        if before != etree.tostring(package.find(OPF + tag)):
            raise ValueError(f"Optimization changed package {tag}")
    for name, before in signatures.items():
        if content_signature(parse_xml(files[name])) != before:
            raise ValueError(
                f"Optimization changed text, IDs, links or image count: {name}"
            )
    files[package_path] = serialize(package, files[package_path])
    files["mimetype"] = b"application/epub+zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent, suffix=".epub", delete=False
        ) as stream:
            temporary = Path(stream.name)
        with zipfile.ZipFile(temporary, "w") as archive:
            for name in sorted(files, key=lambda value: (value != "mimetype", value)):
                info = (
                    copy.copy(infos[name])
                    if name in infos
                    else zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                )
                archive.writestr(
                    info,
                    files[name],
                    compress_type=(
                        zipfile.ZIP_STORED
                        if name == "mimetype"
                        else zipfile.ZIP_DEFLATED
                    ),
                    compresslevel=9,
                )
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("Output ZIP integrity check failed")
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    report.update(
        output=str(output),
        output_bytes=output.stat().st_size,
        output_sha256=digest(output.read_bytes()),
        removed_resources=sorted(unused),
        pruning_skipped=pruning_note,
        content_preserved=True,
        reduction_percent=round(
            100 * (1 - output.stat().st_size / len(source_data)), 2
        ),
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "output", type=Path, nargs="?", help="Default: INPUT.optimized.epub"
    )
    parser.add_argument(
        "--paperwhite",
        action="store_true",
        help="Grayscale interior images and 16-gray opaque PNGs; keep cover color",
    )
    parser.add_argument(
        "--photo",
        action="append",
        default=[],
        metavar="GLOB",
        help="Reviewed photograph ZIP-path glob (repeatable); use 632 x 840 bound",
    )
    parser.add_argument(
        "--keep-svg",
        action="store_true",
        help="Preserve vector illustrations instead of rasterizing static SVG images",
    )
    parser.add_argument(
        "--fix-markup",
        action="store_true",
        help="Repair multi-heading hgroup and redundant heading labels in EPUB nav",
    )
    parser.add_argument("--jpeg-quality", type=int, default=85)
    parser.add_argument("--report", type=Path, help="Default: OUTPUT.optimization.json")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing output/report files; input is never overwritten",
    )
    args = parser.parse_args()
    output = args.output or args.input.with_name(args.input.stem + ".optimized.epub")
    report_path = args.report or output.with_suffix(".optimization.json")
    for path in (args.input, output):
        if (
            report_path.resolve() == path.resolve()
            or report_path.exists()
            and path.exists()
            and os.path.samefile(report_path, path)
        ):
            parser.error("Report path must differ from input and output")
    if report_path.exists() and not args.force:
        parser.error(f"Report exists: {report_path}; use --force to replace it")
    policy = Policy(
        args.paperwhite,
        tuple(args.photo),
        args.keep_svg,
        args.fix_markup,
        args.jpeg_quality,
    )
    try:
        report = optimize(args.input, output, policy, force=args.force)
    except (
        ValueError,
        OSError,
        KeyError,
        etree.XMLSyntaxError,
        subprocess.SubprocessError,
    ) as exc:
        parser.exit(1, f"EPUB optimization failed: {exc}\n")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        f"{report['source_bytes']:,} -> {report['output_bytes']:,} bytes ({report['reduction_percent']}% smaller): {output}"
    )
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
