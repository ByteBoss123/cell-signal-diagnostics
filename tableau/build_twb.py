"""Generate tableau/cell_signal_diagnostics.twb: 4 worksheets + 1 dashboard on the CSVs in outputs/tables/.

The text connections point at ../outputs/tables relative to this folder. If Tableau cannot find
the files when the workbook is opened, point the data source at outputs/tables/ once.
"""
from pathlib import Path
from xml.sax.saxutils import quoteattr as q

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TABLES = ROOT / "outputs" / "tables"
DTYPE = {"int64": ("integer", "measure", "quantitative"), "float64": ("real", "measure", "quantitative"),
         "object": ("string", "dimension", "nominal"), "str": ("string", "dimension", "nominal"),
         "bool": ("boolean", "dimension", "nominal")}
DIMS = {"cell_id", "source", "context", "worst_signal", "fault", "variant", "reject", "naive_flag", "in_recovery", "shared_cause"}
DATES = set()


def datasource(name, csv, calcs=()):
    df = pd.read_csv(TABLES / csv, nrows=200)
    rel_cols, meta_cols = [], []
    for i, c in enumerate(df.columns):
        if c in DATES:
            dt, role, typ = "date", "dimension", "ordinal"
        else:
            dt, role, typ = DTYPE.get(str(df[c].dtype), ("string", "dimension", "nominal"))
            if c in DIMS:
                role, typ = "dimension", "nominal" if dt == "string" else "ordinal"
        rel_cols.append(f"<column datatype='{dt}' name={q(c)} ordinal='{i}' />")
        meta_cols.append(f"<column datatype='{dt}' name={q('[' + c + ']')} role='{role}' type='{typ}' />")
    calc_xml = "".join(
        f"<column caption={q(cap)} datatype='{dt}' name={q(nm)} role='{role}' type='{typ}'><calculation class='tableau' formula={q(f)} /></column>"
        for nm, cap, dt, role, typ, f in calcs)
    table = "[" + csv.replace(".", "#") + "]"
    return (f"<datasource caption={q(name)} inline='true' name='federated.{name.lower().replace(' ', '_')}' version='18.1'>"
            f"<connection class='federated'><named-connections>"
            f"<named-connection caption={q(csv)} name='textscan.{csv.split('.')[0]}'>"
            f"<connection class='textscan' directory='../outputs/tables' filename={q(csv)} password='' server='' /></named-connection>"
            f"</named-connections><relation connection='textscan.{csv.split('.')[0]}' name={q(csv)} table={q(table)} type='table'>"
            f"<columns character-set='UTF-8' header='yes' locale='en_US' separator=','>{''.join(rel_cols)}</columns></relation></connection>"
            f"{''.join(meta_cols)}{calc_xml}</datasource>")


def ci(col, deriv, typ, short):
    pivot = {"nominal": "nk", "ordinal": "ok", "quantitative": "qk"}[typ]
    return f"[{short}:{col}:{pivot}]", f"<column-instance column='[{col}]' derivation='{deriv}' name='[{short}:{col}:{pivot}]' pivot='key' type='{typ}' />"


def worksheet(name, ds, cols, rows, mark, color=None, lod=None, filt=None):
    """cols/rows: lists of (column, derivation, type, short)."""
    used = {}
    for spec in [*cols, *rows, *([color] if color else []), *([lod] if lod else [])]:
        ref, xml = ci(*spec)
        used[ref] = xml
    shelf = lambda specs: " / ".join(f"[{ds}].{ci(*s)[0]}" for s in specs)  # noqa: E731
    enc = ""
    if color or lod:
        enc = "<encodings>" + (f"<color column='[{ds}].{ci(*color)[0]}' />" if color else "") + \
              (f"<lod column='[{ds}].{ci(*lod)[0]}' />" if lod else "") + "</encodings>"
    filt_xml = ""
    if filt:
        col, val = filt
        filt_xml = (f"<filter class='categorical' column='[{ds}].[none:{col}:nk]'><groupfilter function='member' level='[none:{col}:nk]' "
                    f"member={q(val)} user:ui-domain='database' user:ui-enumeration='inclusive' user:ui-marker='enumerate' /></filter>")
        used.setdefault(f"[none:{col}:nk]", ci(col, "None", "nominal", "none")[1])
    return (f"<worksheet name={q(name)}><table><view><datasources><datasource name='{ds}' /></datasources>"
            f"<datasource-dependencies datasource='{ds}'>{''.join(used.values())}</datasource-dependencies>{filt_xml}"
            f"<aggregation value='true' /></view><style /><panes><pane selection-relaxation-option='selection-relaxation-allow'>"
            f"<view><breakdown value='auto' /></view><mark class='{mark}' />{enc}</pane></panes>"
            f"<rows>{shelf(rows)}</rows><cols>{shelf(cols)}</cols></table><simple-id uuid='{{{uid(name)}}}' /></worksheet>")


def uid(s):
    import uuid
    return str(uuid.uuid5(uuid.NAMESPACE_URL, s)).upper()


NAIVE = ("[Calculation_20261113190000_001]", "Naive alarm", "integer", "measure", "quantitative",
         "IF [naive_flag] THEN 1 ELSE 0 END")
REJECT = ("[Calculation_20261113190000_002]", "Cell reject", "string", "dimension", "nominal",
          "IF [reject] AND [shared_cause] THEN 'reject: shared rig cause' ELSEIF [reject] THEN 'reject: single cell' "
          "ELSEIF [context] <> 'ok' THEN 'test artifact: ' + [context] ELSE 'normal' END")
DS = [("Cycles", "cycle_signals_scored.csv", [NAIVE, REJECT]), ("Injection", "injection_detection.csv", [])]
ds = {n: f"federated.{n.lower().replace(' ', '_')}" for n, _, _ in DS}
CELL = ("cell_id", "None", "nominal", "none")
CYCLE = ("cycle", "None", "quantitative", "none")
sheets = [
    worksheet("Capacity by Cycle", ds["Cycles"], [CYCLE], [CELL, ("cap_ah", "Avg", "quantitative", "avg")], "Circle",
              color=("Calculation_20261113190000_002", "None", "nominal", "none")),
    worksheet("Detector Score", ds["Cycles"], [CYCLE], [CELL, ("score", "Avg", "quantitative", "avg")], "Circle",
              color=("Calculation_20261113190000_002", "None", "nominal", "none")),
    worksheet("Naive Alarms by Cause", ds["Cycles"], [("Calculation_20261113190000_001", "Sum", "quantitative", "sum")],
              [("context", "None", "nominal", "none")], "Bar"),
    worksheet("Injected Fault Detection", ds["Injection"], [("fault", "None", "nominal", "none"), ("size", "None", "quantitative", "none")],
              [("detection_rate", "Avg", "quantitative", "avg")], "Line", color=("source", "None", "nominal", "none")),
]
names = ["Capacity by Cycle", "Detector Score", "Naive Alarms by Cause", "Injected Fault Detection"]
zones = [(4, 0, 40, 600, 620, names[0]), (5, 600, 40, 600, 620, names[1]), (6, 0, 660, 400, 340, names[2]),
         (7, 400, 660, 800, 340, names[3])]
DASH = "Cell Signal Diagnostics"
dash = (f"<dashboard name={q(DASH)}><size maxheight='1000' maxwidth='1200' minheight='1000' minwidth='1200' sizing-mode='fixed' />"
        "<datasources /><zones><zone h='1000' id='2' layout-strategy-id='flow' type-v2='layout-basic' w='1200' x='0' y='0'>"
        "<zone h='40' id='3' type-v2='title' w='1200' x='0' y='0' />" +
        "".join(f"<zone h='{h}' id='{i}' name={q(n)} type-v2='visual' w='{w}' x='{x}' y='{y}' />" for i, x, y, w, h, n in zones) +
        f"</zone></zones><simple-id uuid='{{{uid('dash')}}}' /></dashboard>")
windows = "".join(f"<window class='worksheet' maximized='true' name={q(n)}><cards /><simple-id uuid='{{{uid('w' + n)}}}' /></window>" for n in names)
windows += (f"<window class='dashboard' maximized='true' name={q(DASH)}><viewpoints>" +
            "".join(f"<viewpoint name={q(n)} />" for n in names) + f"</viewpoints><active id='-1' /><simple-id uuid='{{{uid('wdash')}}}' /></window>")
twb = ("<?xml version='1.0' encoding='utf-8' ?>\n<!-- build 20262.26.0528.2205 -->\n"
       "<workbook original-version='18.1' source-build='2026.2.0 (20262.26.0528.2205)' source-platform='win' version='18.1' "
       "xmlns:user='http://www.tableausoftware.com/xml/user'><document-format-change-manifest><SheetIdentifierTracking /><SortTagCleanup />"
       "<WindowsPersistSimpleIdentifiers /></document-format-change-manifest><preferences />"
       f"<datasources>{''.join(datasource(n, f, c) for n, f, c in DS)}</datasources>"
       f"<worksheets>{''.join(sheets)}</worksheets><dashboards>{dash}</dashboards><windows>{windows}</windows></workbook>")
out = ROOT / "tableau" / "cell_signal_diagnostics.twb"
out.write_text(twb, encoding="utf-8")
print(out)
