"""cat_loader.py - Carga y previsualización de planos DWG/DXF/PDF para ComCAD V1.

Características:
- DWG, DXF, PDF
- Conversión DWG → DXF vía ODA (si disponible)
- Extracción de primitivas:
  LINE, (LW)POLYLINE, CIRCLE, ARC, TEXT (opcional), expansión de bloques INSERT
- Metadatos: lineweight (lw en 1/100 mm), linetype (lt), info de bloque

Mejoras:
- Colores ACI reales (BYLAYER/BYBLOCK), grosor/tipo de línea efectivos.
- Transformación correcta de INSERT (rotación/escala/base point).
- Soporte básico para HATCH (loops) y MTEXT (plain_text).
- Ignora capas no visibles e entidades invisibles/vacías.
- Extents calculados si no están en encabezado.

Limitaciones:
- Linetype patrón detallado no interpretado (solo nombre efectivo).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import subprocess
import math
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple, Union

try:
    import ezdxf  # type: ignore
    from ezdxf.lldxf.const import DXFStructureError  # type: ignore
    from ezdxf import const as DXFCONST  # type: ignore
except Exception:  # pragma: no cover
    ezdxf = None  # type: ignore
    DXFStructureError = Exception  # type: ignore
    DXFCONST = None  # type: ignore

# Colores ACI (compat con distintas versiones)
try:
    # ezdxf >= 1.0
    from ezdxf.colors import aci2rgb as _aci2rgb  # type: ignore
    def _aci_to_rgb(c: int) -> tuple:
        return _aci2rgb(c)
except Exception:
    try:
        # Algunos entornos exponen aci_to_rgb
        from ezdxf.colors import aci_to_rgb as _aci2rgb_alt  # type: ignore
        def _aci_to_rgb(c: int) -> tuple:
            return _aci2rgb_alt(c)
    except Exception:
        def _aci_to_rgb(c: int) -> tuple:  # pragma: no cover
            # Fallback neutro (gris) si no hay tabla ACI disponible
            return (200, 200, 210)

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None  # type: ignore

try:
    import PyPDF2  # type: ignore
except Exception:  # pragma: no cover
    PyPDF2 = None  # type: ignore

from PyQt5.QtWidgets import QFileDialog, QWidget, QGraphicsView
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import Qt

SUPPORTED_EXTENSIONS = {".dwg", ".dxf", ".pdf"}


# ===================== Data Structures ===================== #
@dataclass
class DwgLayerInfo:
    name: str
    color: Optional[int] = None
    frozen: Optional[bool] = None
    locked: Optional[bool] = None
    entity_count: int = 0


@dataclass
class DwgInfo:
    path: str
    layers: List[DwgLayerInfo] = field(default_factory=list)
    total_entities: int = 0
    model_space_entity_types: Dict[str, int] = field(default_factory=dict)
    extents: Optional[Tuple[float, float, float, float]] = None
    library: str = "ezdxf"
    source_was_converted: bool = False
    converted_temp_path: Optional[str] = None
    original_path: Optional[str] = None


@dataclass
class PdfPreview:
    path: str
    page_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    first_page_pixmap: Optional[QPixmap] = None
    library: str = "PyMuPDF|PyPDF2"


@dataclass
class LoadError:
    path: Optional[str]
    message: str
    detail: Optional[str] = None


@dataclass
class LoadResult:
    path: str
    type: str  # 'dwg' | 'dxf' | 'pdf'
    dwg: Optional[DwgInfo] = None
    pdf: Optional[PdfPreview] = None
    error: Optional[LoadError] = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class Primitive:
    tipo: str              # 'LINE'|'POLYLINE'|'CIRCLE'|'ARC'|'TEXT'|'MTEXT'|'HATCH'
    layer: str
    color: tuple           # (r,g,b) color efectivo ACI/BYLAYER/BYBLOCK resuelto
    data: dict             # {geom... , 'lw': int, 'lt': str, ...}
    is_block: bool = False
    block_name: Optional[str] = None


# ===================== Public API ===================== #
def abrir_archivo(parent: Optional[QWidget] = None, start_dir: Optional[str] = None) -> Optional[str]:
    if start_dir is None:
        start_dir = os.path.expanduser("~")
    filtro = "Planos (*.dwg *.dxf *.pdf);;DWG (*.dwg);;DXF (*.dxf);;PDF (*.pdf);;Todos (*.*)"
    path, _ = QFileDialog.getOpenFileName(parent, "Abrir plano", start_dir, filtro)
    return path or None


def cargar_archivo(path: str, preview_pdf_max_px: int = 1600) -> LoadResult:
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return LoadResult(path=path, type="unknown",
                          error=LoadError(path=path, message=f"Extensión no soportada: {ext}"))

    if ext == ".dxf":
        info = _cargar_dxf(path)
        if isinstance(info, LoadError):
            return LoadResult(path=path, type="dxf", error=info)
        return LoadResult(path=path, type="dxf", dwg=info)

    if ext == ".dwg":
        info = _cargar_dwg(path)
        if isinstance(info, LoadError):
            return LoadResult(path=path, type="dwg", error=info)
        return LoadResult(path=path, type="dwg", dwg=info)

    if ext == ".pdf":
        pdf_info = _cargar_pdf(path, preview_pdf_max_px)
        if isinstance(pdf_info, LoadError):
            return LoadResult(path=path, type="pdf", error=pdf_info)
        return LoadResult(path=path, type="pdf", pdf=pdf_info)

    return LoadResult(path=path, type="unknown",
                      error=LoadError(path=path, message="Tipo no manejado"))


# ===================== DXF / DWG base ===================== #
def _cargar_dxf(path: str) -> Union[DwgInfo, LoadError]:
    if ezdxf is None:
        return LoadError(path=path, message="La librería ezdxf no está instalada.", detail="pip install ezdxf")
    if not os.path.isfile(path):
        return LoadError(path=path, message="Archivo no encontrado")
    try:
        return _parse_dxf(path, converted=False, original_path=path)
    except DXFStructureError as e:
        return LoadError(path=path, message="DXF corrupto o incompatible", detail=str(e))
    except Exception as e:
        return LoadError(path=path, message="Error al cargar DXF", detail=str(e))


def _cargar_dwg(path: str) -> Union[DwgInfo, LoadError]:
    if ezdxf is None:
        return LoadError(path=path, message="La librería ezdxf no está instalada.", detail="pip install ezdxf")
    if not os.path.isfile(path):
        return LoadError(path=path, message="Archivo no encontrado")

    try:
        return _parse_dxf(path, converted=False, original_path=path)
    except DXFStructureError as e:
        if "not a dxf" in str(e).lower() or "invalid dxf" in str(e).lower():
            conv, err = _convert_dwg_to_dxf(path)
            if not conv:
                return LoadError(path=path, message="No se pudo convertir DWG automáticamente", detail=err)
            try:
                info = _parse_dxf(conv, converted=True, original_path=path)
                info.converted_temp_path = conv
                return info
            except Exception as e2:
                return LoadError(path=path, message="Error tras conversión DWG→DXF", detail=str(e2))
        return LoadError(path=path, message="Error al cargar DWG", detail=str(e))
    except Exception as e:
        return LoadError(path=path, message="Error al cargar DWG", detail=str(e))


def _parse_dxf(path: str, converted: bool, original_path: str) -> DwgInfo:
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    layer_table = doc.layers

    layers_result: List[DwgLayerInfo] = []
    for layer in layer_table:
        try:
            ent_count = sum(1 for _ in msp.query(f'* [layer=="{layer.dxf.name}"]'))
        except Exception:
            ent_count = 0
        layers_result.append(DwgLayerInfo(
            name=layer.dxf.name,
            color=getattr(layer.dxf, "color", None),
            frozen=getattr(layer, "is_frozen", None),
            locked=getattr(layer, "is_locked", None),
            entity_count=ent_count,
        ))

    type_counts: Dict[str, int] = {}
    total = 0
    for e in msp:
        t = e.dxftype()
        type_counts[t] = type_counts.get(t, 0) + 1
        total += 1

    # Extents: header -> bbox() -> None
    ext: Optional[Tuple[float, float, float, float]] = None
    try:
        extmin = doc.header.get("$EXTMIN")
        extmax = doc.header.get("$EXTMAX")
        if extmin and extmax:
            (xmin, ymin, _zmin) = extmin
            (xmax, ymax, _zmax) = extmax
            ext = (float(xmin), float(ymin), float(xmax), float(ymax))
    except Exception:
        ext = None
    if ext is None:
        try:
            bbox = msp.bbox()  # ezdxf >= 1.0
            if hasattr(bbox, "extmin") and hasattr(bbox, "extmax"):
                (xmin, ymin, _), (xmax, ymax, _) = bbox.extmin, bbox.extmax
                ext = (float(xmin), float(ymin), float(xmax), float(ymax))
        except Exception:
            ext = None

    return DwgInfo(
        path=path,
        original_path=original_path,
        layers=layers_result,
        total_entities=total,
        model_space_entity_types=type_counts,
        extents=ext,
        source_was_converted=converted,
    )


def _convert_dwg_to_dxf(path: str):
    converter = _find_oda_converter()
    if not converter:
        return None, "Instala ODA File Converter o define ODA_CONVERTER."
    if not os.path.isfile(path):
        return None, "Archivo origen no encontrado."

    root = tempfile.mkdtemp(prefix="comcad_dwg2dxf_")
    in_dir = os.path.join(root, "in")
    out_dir = os.path.join(root, "out")
    os.makedirs(in_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    base = os.path.basename(path)
    shutil.copy2(path, os.path.join(in_dir, base))

    versions = ["R2013", "ACAD2013", "R2010", "R2007"]
    last_err = None
    for v in versions:
        cmd = [converter, in_dir, out_dir, "all", v, "0", "0"]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
            if proc.returncode != 0:
                last_err = f"Exit={proc.returncode} Err={proc.stderr.strip()}"
                continue
            dxf = _find_converted_dxf(out_dir, base)
            if dxf:
                return dxf, None
            last_err = "No se halló DXF tras conversión."
        except Exception as e:
            last_err = str(e)
            continue
    return None, f"Fallo conversión ODA: {last_err}"


def _find_oda_converter() -> Optional[str]:
    env = os.environ.get("ODA_CONVERTER")
    if env and os.path.isfile(env):
        return env
    roots = [
        r"C:\Program Files\ODA",
        r"C:\Program Files (x86)\ODA",
        r"C:\Program Files\ODA File Converter",
        r"C:\Program Files (x86)\ODA File Converter",
    ]
    exe = "ODAFileConverter.exe"
    for root in roots:
        if os.path.isdir(root):
            for dirpath, _d, files in os.walk(root):
                if exe in files:
                    return os.path.join(dirpath, exe)
    return None


def _find_converted_dxf(out_dir: str, original_name: str) -> Optional[str]:
    base_no_ext = os.path.splitext(original_name)[0].lower()
    for f in os.listdir(out_dir):
        if f.lower().endswith(".dxf") and base_no_ext in f.lower():
            return os.path.join(out_dir, f)
    dxfs = [f for f in os.listdir(out_dir) if f.lower().endswith(".dxf")]
    if len(dxfs) == 1:
        return os.path.join(out_dir, dxfs[0])
    return None


# ===================== PDF ===================== #
def _cargar_pdf(path: str, preview_pdf_max_px: int) -> Union[PdfPreview, LoadError]:
    if not os.path.isfile(path):
        return LoadError(path=path, message="Archivo no encontrado")
    if fitz is not None:
        try:
            doc = fitz.open(path)
            meta = dict(doc.metadata or {})
            pc = doc.page_count
            pm_qt = None
            if pc > 0:
                page = doc.load_page(0)
                rect = page.rect
                max_side = max(rect.width, rect.height)
                zoom = 1.0
                if max_side < 600:
                    zoom = 600 / max_side
                elif max_side > 2000:
                    zoom = 2000 / max_side
                mat = fitz.Matrix(zoom, zoom)
                pm = page.get_pixmap(alpha=False, matrix=mat)
                img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format_RGB888)
                pm_qt = QPixmap.fromImage(img.copy())
                if pm_qt and max(pm_qt.width(), pm_qt.height()) > preview_pdf_max_px:
                    pm_qt = pm_qt.scaled(preview_pdf_max_px, preview_pdf_max_px,
                                         Qt.KeepAspectRatio, Qt.SmoothTransformation)
            doc.close()
            return PdfPreview(path=path, page_count=pc, metadata=meta, first_page_pixmap=pm_qt, library="PyMuPDF")
        except Exception as e:
            if PyPDF2 is None:
                return LoadError(path=path, message="Error PyMuPDF", detail=str(e))
    if PyPDF2 is not None:
        try:
            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                meta = dict(reader.metadata or {})
                pc = len(reader.pages)
            return PdfPreview(path=path, page_count=pc, metadata=meta, first_page_pixmap=None, library="PyPDF2")
        except Exception as e:
            return LoadError(path=path, message="Error PyPDF2", detail=str(e))
    return LoadError(path=path, message="No hay librerías PDF disponibles")


# ===================== Utilidades de DXF (visibilidad/atributos) ===================== #
def _layer_is_visible(doc, layer_name: str) -> bool:
    try:
        lay = doc.layers.get(layer_name)
    except Exception:
        return True
    if getattr(lay, "is_off", False):
        return False
    if getattr(lay, "is_frozen", False):
        return False
    return True


def _effective_color(doc, entity, byblock_color: Optional[tuple]) -> tuple:
    """Devuelve color RGB efectivo respetando BYLAYER/BYBLOCK."""
    try:
        aci = entity.dxf.color if entity.dxf.hasattr("color") else None
    except Exception:
        aci = None

    # BYBLOCK: 0
    if DXFCONST and aci in (DXFCONST.BYBLOCK, 0):
        if byblock_color is not None:
            return byblock_color
        aci = DXFCONST.BYLAYER  # fallback a BYLAYER

    # BYLAYER: 256
    if DXFCONST and (aci is None or aci in (DXFCONST.BYLAYER, 256)):
        try:
            lay = doc.layers.get(entity.dxf.layer)
            aci = getattr(lay.dxf, "color", 7) if hasattr(lay, "dxf") else 7
        except Exception:
            aci = 7  # blanco/negro
    try:
        return _aci_to_rgb(int(aci if aci is not None else 7))
    except Exception:
        return (200, 200, 210)


def _effective_lineweight(doc, entity) -> Tuple[int, str]:
    """Devuelve (lw_1_100_mm, src). Resuelve BYLAYER/BYBLOCK a valor de capa cuando sea posible."""
    src = "ENTITY"
    try:
        lw = getattr(entity.dxf, "lineweight", None)
    except Exception:
        lw = None

    # Valores especiales: BYLAYER (-1), BYBLOCK (-2), DEFAULT (-3)
    specials = {-1, -2, -3, None}
    if lw in specials:
        try:
            lay = doc.layers.get(entity.dxf.layer)
            lw_layer = getattr(lay.dxf, "lineweight", 0)
            lw = lw_layer if lw_layer not in specials else 0
            src = "LAYER"
        except Exception:
            lw = 0
            src = "DEFAULT"

    # Asegurar entero
    try:
        lw = int(lw or 0)
    except Exception:
        lw = 0
    return lw, src


def _effective_linetype(doc, entity) -> Tuple[str, str]:
    """Devuelve (lt_name, src) resolviendo BYLAYER/BYBLOCK a nombre de capa si aplica."""
    src = "ENTITY"
    try:
        lt = getattr(entity.dxf, "linetype", None)
    except Exception:
        lt = None
    if not lt or lt.upper() in ("BYLAYER", "BYBLOCK"):
        try:
            lay = doc.layers.get(entity.dxf.layer)
            lt_layer = getattr(lay.dxf, "linetype", "CONTINUOUS")
            lt = lt_layer or "CONTINUOUS"
            src = "LAYER"
        except Exception:
            lt = "CONTINUOUS"
            src = "DEFAULT"
    return str(lt), src


# ===================== Extracción de primitivas ===================== #
def extraer_primitivas_basicas(path: str,
                               limitar: Optional[int] = None,
                               expand_blocks: bool = True,
                               max_block_repeats: int = 8000,
                               max_total_after_blocks: int = 250000,
                               incluir_texto: bool = True,
                               incluir_hatch: bool = True) -> List[Primitive]:
    """
    Obtiene primitivas básicas (incluyendo entidades dentro de INSERT).
    Retorna lista de Primitive. TEXT/MTEXT y HATCH se incluyen si están habilitados.

    Límites:
        - limitar: corte antes de expandir (modelspace directo)
        - max_block_repeats: protege contra explosión de instancias
        - max_total_after_blocks: límite duro final
    """
    if ezdxf is None or not os.path.isfile(path):
        return []
    try:
        doc = ezdxf.readfile(path)
    except Exception:
        return []

    msp = doc.modelspace()
    from ezdxf.math import Matrix44
    prims: List[Primitive] = []
    cuenta = 0
    block_repeats: Dict[str, int] = {}

    def entidad_visible(e) -> bool:
        try:
            if getattr(e.dxf, "invisible", 0) in (1, True):
                return False
            layer = getattr(e.dxf, "layer", "0")
            if not _layer_is_visible(doc, layer):
                return False
        except Exception:
            pass
        return True

    def add_primitive(tipo, layer, col, data, is_block=False, block_name=None):
        nonlocal cuenta
        prims.append(Primitive(tipo=tipo, layer=layer, color=col, data=data,
                               is_block=is_block, block_name=block_name))
        cuenta += 1

    def apply_point(pt, m: Matrix44 | None):
        if m:
            # pt puede ser tuple de 2 o 3
            x = pt[0]
            y = pt[1]
            z = pt[2] if len(pt) > 2 else 0.0
            v = m.transform((x, y, z))
            return (float(v[0]), float(v[1]), float(v[2]))
        return (float(pt[0]), float(pt[1]), float(pt[2] if len(pt) > 2 else 0.0))

    def discretizar_arco(cx, cy, r, start_deg, end_deg, ccw=True, seg=32):
        a0 = math.radians(start_deg)
        a1 = math.radians(end_deg)
        if ccw:
            while a1 <= a0:
                a1 += 2 * math.pi
        else:
            while a0 <= a1:
                a0 += 2 * math.pi
        steps = max(4, seg)
        pts = []
        for i in range(steps + 1):
            t = i / steps
            ang = a0 + (a1 - a0) * t
            pts.append((cx + math.cos(ang) * r, cy + math.sin(ang) * r))
        return pts

    def hatch_loops(e) -> List[List[Tuple[float, float]]]:
        loops: List[List[Tuple[float, float]]] = []
        try:
            for bpath in e.paths:
                current: List[Tuple[float, float]] = []
                first_set = False
                first_pt: Optional[Tuple[float, float]] = None
                for edge in bpath:
                    t = getattr(edge, "EDGE_TYPE", "")
                    if t == "LineEdge":
                        x1, y1 = edge.start[0], edge.start[1]
                        x2, y2 = edge.end[0], edge.end[1]
                        if not first_set:
                            current.append((x1, y1))
                            first_pt = (x1, y1)
                            first_set = True
                        current.append((x2, y2))
                    elif t == "ArcEdge":
                        cx, cy = edge.center[0], edge.center[1]
                        r = edge.radius
                        start = math.degrees(edge.start_angle)
                        end = math.degrees(edge.end_angle)
                        pts = discretizar_arco(cx, cy, r, start, end, ccw=not edge.ccw, seg=28)
                        if not first_set and pts:
                            current.append(pts[0])
                            first_pt = pts[0]
                            first_set = True
                        current.extend(pts[1:])
                    elif t == "EllipseEdge":
                        cx, cy = edge.center
                        ratio = edge.ratio
                        mx, my = edge.major_axis
                        a = math.hypot(mx, my)
                        b = a * ratio
                        ang = math.degrees(math.atan2(my, mx))
                        start = edge.start_param
                        end = edge.end_param
                        steps = 48
                        for i in range(steps + 1):
                            tt = start + (end - start) * (i / steps)
                            x = a * math.cos(tt)
                            y = b * math.sin(tt)
                            xr = x * math.cos(math.radians(ang)) - y * math.sin(math.radians(ang))
                            yr = x * math.sin(math.radians(ang)) + y * math.cos(math.radians(ang))
                            px, py = cx + xr, cy + yr
                            if not first_set:
                                current.append((px, py))
                                first_pt = (px, py)
                                first_set = True
                            else:
                                current.append((px, py))
                if current:
                    # Cierra el loop si está abierto
                    if first_pt and (abs(current[-1][0]-first_pt[0]) > 1e-6 or abs(current[-1][1]-first_pt[1]) > 1e-6):
                        current.append(first_pt)
                    loops.append(current)
        except Exception:
            pass
        return loops

    # Nota: propagamos byblock_color hacia entidades hijas de INSERT
    def entity_to_prims(e, transform: Matrix44 | None, depth=0, block_name=None, byblock_color: Optional[tuple] = None):
        nonlocal cuenta
        if limitar and cuenta >= limitar:
            return
        if cuenta >= max_total_after_blocks:
            return
        try:
            if not entidad_visible(e):
                return
        except Exception:
            pass

        dxftype = e.dxftype()
        layer = getattr(e.dxf, "layer", "0")

        col = _effective_color(doc, e, byblock_color)
        lw_val, lw_src = _effective_lineweight(doc, e)
        lt_val, lt_src = _effective_linetype(doc, e)

        def base_data():
            return {"lw": lw_val, "lw_raw": getattr(e.dxf, "lineweight", None) or 0,
                    "lt": lt_val, "lt_raw": getattr(e.dxf, "linetype", None) or "BYLAYER"}

        try:
            if dxftype == "LINE":
                p1 = apply_point(e.dxf.start, transform)
                p2 = apply_point(e.dxf.end, transform)
                add_primitive("LINE", layer, col, {
                    **base_data(),
                    "x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1]
                }, block_name is not None, block_name)

            elif dxftype in ("LWPOLYLINE", "POLYLINE"):
                pts: List[Tuple[float, float]] = []
                closed = False
                if dxftype == "LWPOLYLINE":
                    # e.__iter__() recorre vértices LWPOLYLINE
                    for v in e:
                        pt = apply_point((v.dxf.x, v.dxf.y, 0.0), transform)
                        pts.append((pt[0], pt[1]))
                    closed = bool(e.closed)
                else:
                    for v in e.vertices:
                        loc = v.dxf.location
                        pt = apply_point((loc.x, loc.y, loc.z), transform)
                        pts.append((pt[0], pt[1]))
                    closed = bool(e.is_closed)
                if pts:
                    add_primitive("POLYLINE", layer, col, {
                        **base_data(),
                        "pts": pts,
                        "closed": closed
                    }, block_name is not None, block_name)

            elif dxftype == "CIRCLE":
                center = apply_point(e.dxf.center, transform)
                add_primitive("CIRCLE", layer, col, {
                    **base_data(),
                    "cx": center[0], "cy": center[1], "r": float(e.dxf.radius)
                }, block_name is not None, block_name)

            elif dxftype == "ARC":
                center = apply_point(e.dxf.center, transform)
                add_primitive("ARC", layer, col, {
                    **base_data(),
                    "cx": center[0], "cy": center[1], "r": float(e.dxf.radius),
                    "start": float(e.dxf.start_angle), "end": float(e.dxf.end_angle)
                }, block_name is not None, block_name)

            elif incluir_texto and dxftype == "TEXT":
                ins = apply_point(e.dxf.insert, transform)
                h = float(getattr(e.dxf, "height", 2.5) or 2.5)
                rot = float(getattr(e.dxf, "rotation", 0.0) or 0.0)
                txt = str(getattr(e.dxf, "text", "") or "")
                if txt.strip():
                    add_primitive("TEXT", layer, col, {
                        **base_data(),
                        "x": ins[0], "y": ins[1],
                        "h": h,
                        "rot": rot,
                        "value": txt
                    }, block_name is not None, block_name)

            elif incluir_texto and dxftype == "MTEXT":
                # Soporte básico para MTEXT
                ins = apply_point(e.dxf.insert, transform)
                rot = float(getattr(e.dxf, "rotation", 0.0) or 0.0)
                ch = float(getattr(e.dxf, "char_height", 2.5) or 2.5)
                try:
                    content = e.plain_text()
                except Exception:
                    content = str(getattr(e.dxf, "text", "") or "")
                if content.strip():
                    add_primitive("MTEXT", layer, col, {
                        **base_data(),
                        "x": ins[0], "y": ins[1],
                        "char_height": ch,
                        "rot": rot,
                        "value": content
                    }, block_name is not None, block_name)

            elif incluir_hatch and dxftype == "HATCH":
                loops = hatch_loops(e)
                if loops:
                    solid = bool(getattr(e.dxf, "solid_fill", 0))
                    add_primitive("HATCH", layer, col, {
                        **base_data(),
                        "loops": loops,     # List[List[(x,y)]]
                        "solid": solid,
                    }, block_name is not None, block_name)

            elif expand_blocks and dxftype == "INSERT":
                # Protección de profundidad y explosión
                if depth > 8:
                    return
                name = e.dxf.name
                block_repeats[name] = block_repeats.get(name, 0) + 1
                if block_repeats[name] > max_block_repeats:
                    return

                try:
                    # Matriz completa del INSERT (incluye translación, rotación, escala y base point)
                    m = e.matrix44()
                except Exception:
                    m = None
                try:
                    blk = doc.blocks.get(name)
                except Exception:
                    blk = None

                # Color BYBLOCK del INSERT para herencia de hijos
                col_byblock = _effective_color(doc, e, byblock_color=None)

                if blk:
                    # Composición: si hay transform previo, parent @ current
                    next_transform = m if transform is None else transform @ m
                    for be in blk:
                        entity_to_prims(be, next_transform, depth + 1, block_name=name, byblock_color=col_byblock)

        except Exception:
            # Ignorar entidad problemática
            return

    # Recorrido
    for ent in msp:
        entity_to_prims(ent, None, 0, None, None)
        if limitar and cuenta >= limitar:
            break
        if cuenta >= max_total_after_blocks:
            break

    return prims


# ===================== Extents y helpers de UI ===================== #
def calcular_extents_primitivas(prims: List[Primitive]) -> Optional[Tuple[float, float, float, float]]:
    """Calcula (xmin, ymin, xmax, ymax) de la lista de primitivas extraídas."""
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")

    def upd(x, y):
        nonlocal xmin, ymin, xmax, ymax
        xmin = min(xmin, x); ymin = min(ymin, y)
        xmax = max(xmax, x); ymax = max(ymax, y)

    for p in prims:
        d = p.data
        t = p.tipo
        try:
            if t == "LINE":
                upd(d["x1"], d["y1"]); upd(d["x2"], d["y2"])
            elif t == "POLYLINE":
                for x, y in d.get("pts", []):
                    upd(x, y)
            elif t == "CIRCLE":
                cx, cy, r = d["cx"], d["cy"], d["r"]
                upd(cx - r, cy - r); upd(cx + r, cy + r)
            elif t == "ARC":
                cx, cy, r = d["cx"], d["cy"], d["r"]
                # Caja del círculo como sobreaproximación
                upd(cx - r, cy - r); upd(cx + r, cy + r)
            elif t in ("TEXT", "MTEXT"):
                upd(d["x"], d["y"])
            elif t == "HATCH":
                for loop in d.get("loops", []):
                    for x, y in loop:
                        upd(x, y)
        except Exception:
            continue

    if xmin == float("inf"):
        return None
    return (xmin, ymin, xmax, ymax)


def aplicar_zoom_extents(view: QGraphicsView, extents: Optional[Tuple[float, float, float, float]], margin: float = 0.08):
    """Centra y ajusta la vista al rectángulo de extents con un margen porcentual."""
    if not extents:
        return
    xmin, ymin, xmax, ymax = extents
    w = max(1e-6, xmax - xmin)
    h = max(1e-6, ymax - ymin)
    # Margen
    dx = w * margin
    dy = h * margin
    from PyQt5.QtCore import QRectF
    rect = QRectF(xmin - dx, ymin - dy, w + 2 * dx, h + 2 * dy)
    view.fitInView(rect, Qt.KeepAspectRatio)


# ===================== Helpers UI ===================== #
def descripcion_corta(result: LoadResult) -> str:
    if not result.ok:
        return f"Error: {result.error.message if result.error else 'desconocido'}"
    if result.type in ('dwg', 'dxf') and result.dwg:
        etiqueta = "DWG" if result.type == 'dwg' else "DXF"
        extra = " (convertido)" if result.dwg.source_was_converted else ""
        return f"{etiqueta}{extra}: {len(result.dwg.layers)} capas, {result.dwg.total_entities} entidades"
    if result.type == 'pdf' and result.pdf:
        return f"PDF: {result.pdf.page_count} páginas"
    return os.path.basename(result.path)


def es_dwg(result: LoadResult) -> bool:
    return result.type in ('dwg', 'dxf') and result.dwg is not None and result.ok


def es_pdf(result: LoadResult) -> bool:
    return result.type == 'pdf' and result.pdf is not None and result.ok


__all__ = [
    'abrir_archivo', 'cargar_archivo', 'descripcion_corta', 'es_dwg', 'es_pdf',
    'DwgInfo', 'PdfPreview', 'LoadResult', 'LoadError',
    'Primitive', 'extraer_primitivas_basicas',
    'calcular_extents_primitivas', 'aplicar_zoom_extents'
]