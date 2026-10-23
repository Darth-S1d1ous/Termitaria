"""最小 pptx。负责人把幻灯片交给控制面，浏览器只从 gateway 下载。"""
from __future__ import annotations

import io
import zipfile

_NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def build_pptx(title: str, slides: list[dict]) -> bytes:
    files: dict[str, str] = {}
    files["[Content_Types].xml"] = _content_types(len(slides))
    files["_rels/.rels"] = _rels_root()
    files["docProps/core.xml"] = _core(title)
    files["docProps/app.xml"] = _app(len(slides))
    files["ppt/theme/theme1.xml"] = _theme()
    files["ppt/slideMasters/slideMaster1.xml"] = _master()
    files["ppt/slideMasters/_rels/slideMaster1.xml.rels"] = _master_rels()
    files["ppt/slideLayouts/slideLayout1.xml"] = _layout()
    files["ppt/slideLayouts/_rels/slideLayout1.xml.rels"] = _layout_rels()
    files["ppt/presentation.xml"] = _presentation(len(slides))
    files["ppt/_rels/presentation.xml.rels"] = _presentation_rels(len(slides))
    for index, slide in enumerate(slides, start=1):
        files[f"ppt/slides/slide{index}.xml"] = _slide(str(slide.get("title") or "Slide"), list(slide.get("bullets") or []))
        files[f"ppt/slides/_rels/slide{index}.xml.rels"] = _slide_rels()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, xml in files.items():
            package.writestr(name, xml.encode("utf-8"))
    return buf.getvalue()


def _x(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _content_types(count: int) -> str:
    overrides = [
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
        '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
    ]
    for index in range(1, count + 1):
        overrides.append(
            f'<Override PartName="/ppt/slides/slide{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        )
    body = "".join(overrides)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f"{body}</Types>"
    )


def _rels_root() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        "</Relationships>"
    )


def _core(title: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{_x(title)}</dc:title><dc:creator>Termitaria</dc:creator>"
        "</cp:coreProperties>"
    )


def _app(count: int) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
        f"<Slides>{count}</Slides><Application>Termitaria</Application>"
        "</Properties>"
    )


def _presentation(count: int) -> str:
    ids = []
    for index in range(count):
        ids.append(f'<p:sldId id="{256 + index}" r:id="rId{index + 2}"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:presentation xmlns:p="{_NS_P}" xmlns:r="{_NS_R}">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        f"<p:sldIdLst>{''.join(ids)}</p:sldIdLst>"
        '<p:sldSz cx="12192000" cy="6858000"/>'
        '<p:notesSz cx="6858000" cy="9144000"/>'
        "</p:presentation>"
    )


def _presentation_rels(count: int) -> str:
    rows = [
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
    ]
    for index in range(1, count + 1):
        rows.append(
            f'<Relationship Id="rId{index + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{index}.xml"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(rows)
        + "</Relationships>"
    )


def _master() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sldMaster xmlns:p="{_NS_P}" xmlns:a="{_NS_A}" xmlns:r="{_NS_R}">'
        "<p:cSld><p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        "<p:grpSpPr/>"
        "</p:spTree></p:cSld>"
        "<p:clrMap bg1=\"lt1\" tx1=\"dk1\" bg2=\"lt2\" tx2=\"dk2\" accent1=\"accent1\" "
        "accent2=\"accent2\" accent3=\"accent3\" accent4=\"accent4\" accent5=\"accent5\" "
        "accent6=\"accent6\" hlink=\"hlink\" folHlink=\"folHlink\"/>"
        "<p:sldLayoutIdLst><p:sldLayoutId id=\"2147483649\" r:id=\"rId1\"/></p:sldLayoutIdLst>"
        "</p:sldMaster>"
    )


def _master_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
        "</Relationships>"
    )


def _layout() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sldLayout xmlns:p="{_NS_P}" xmlns:a="{_NS_A}" xmlns:r="{_NS_R}" type="blank">'
        "<p:cSld><p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        "<p:grpSpPr/>"
        "</p:spTree></p:cSld>"
        "</p:sldLayout>"
    )


def _layout_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
        "</Relationships>"
    )


def _slide_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
        "</Relationships>"
    )


def _slide(title: str, bullets: list[str]) -> str:
    paras = []
    for bullet in bullets:
        paras.append(
            "<a:p><a:pPr marL=\"342900\" indent=\"-171450\"><a:buChar char=\"•\"/></a:pPr>"
            f"<a:r><a:rPr lang=\"en-US\" sz=\"2000\"/><a:t>{_x(bullet[:400])}</a:t></a:r></a:p>"
        )
    if not paras:
        paras.append("<a:p><a:endParaRPr lang=\"en-US\"/></a:p>")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sld xmlns:p="{_NS_P}" xmlns:a="{_NS_A}" xmlns:r="{_NS_R}">'
        "<p:cSld><p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        "<p:grpSpPr/>"
        + _shape(2, "Title", title[:120], 3200, 457200, 274638, 11277600, 1143000)
        + _shape(3, "Body", "", 2000, 457200, 1600200, 11277600, 4525963, "".join(paras))
        + "</p:spTree></p:cSld></p:sld>"
    )


def _shape(sid: int, name: str, text: str, size: int, x: int, y: int, cx: int, cy: int, paras: str = "") -> str:
    if not paras:
        paras = f"<a:p><a:r><a:rPr lang=\"en-US\" sz=\"{size}\"/><a:t>{_x(text)}</a:t></a:r></a:p>"
    return (
        "<p:sp>"
        f'<p:nvSpPr><p:cNvPr id="{sid}" name="{name}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        "<p:spPr>"
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        "</p:spPr>"
        f"<p:txBody><a:bodyPr/><a:lstStyle/>{paras}</p:txBody>"
        "</p:sp>"
    )


def _theme() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<a:theme xmlns:a="{_NS_A}" name="Termitaria">'
        "<a:themeElements>"
        '<a:clrScheme name="Termitaria">'
        '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
        '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
        '<a:dk2><a:srgbClr val="1F4E79"/></a:dk2>'
        '<a:lt2><a:srgbClr val="E7EEF5"/></a:lt2>'
        '<a:accent1><a:srgbClr val="1F4E79"/></a:accent1>'
        '<a:accent2><a:srgbClr val="4FAE4E"/></a:accent2>'
        '<a:accent3><a:srgbClr val="C45911"/></a:accent3>'
        '<a:accent4><a:srgbClr val="7030A0"/></a:accent4>'
        '<a:accent5><a:srgbClr val="00B0F0"/></a:accent5>'
        '<a:accent6><a:srgbClr val="FFC000"/></a:accent6>'
        '<a:hlink><a:srgbClr val="0563C1"/></a:hlink>'
        '<a:folHlink><a:srgbClr val="954F72"/></a:folHlink>'
        "</a:clrScheme>"
        '<a:fontScheme name="Termitaria">'
        '<a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
        '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>'
        "</a:fontScheme>"
        '<a:fmtScheme name="Termitaria">'
        "<a:fillStyleLst>"
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        "</a:fillStyleLst>"
        "<a:lnStyleLst>"
        '<a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
        '<a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
        '<a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
        "</a:lnStyleLst>"
        "<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>"
        "<a:effectStyle><a:effectLst/></a:effectStyle>"
        "<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>"
        "<a:bgFillStyleLst>"
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        "</a:bgFillStyleLst>"
        "</a:fmtScheme>"
        "</a:themeElements>"
        "</a:theme>"
    )
