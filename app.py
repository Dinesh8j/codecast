import streamlit as st
import json, re, io, zipfile, os, sqlite3
from datetime import datetime, date
from typing import Any

st.set_page_config(page_title="CodeCast", page_icon="🎯", layout="wide")

# ─────────────────────────────────────────────────────────────────────────────
# DB layer — Supabase (cloud) or SQLite (local)  — auto-detected
# ─────────────────────────────────────────────────────────────────────────────

def _use_supabase():
    try:
        url = st.secrets.get("SUPABASE_URL","")
        key = st.secrets.get("SUPABASE_KEY","")
        return bool(url and key)
    except:
        return False

def _get_supabase():
    from supabase import create_client
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

# ── SQLite helpers ────────────────────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "codecast.db")

def _sqlite_conn():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def _init_sqlite():
    con = _sqlite_conn()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS feedbacks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            category   TEXT NOT NULL,
            message    TEXT NOT NULL,
            language   TEXT,
            json_used  TEXT,
            status     TEXT NOT NULL DEFAULT 'Open',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stats (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            event      TEXT NOT NULL,
            language   TEXT,
            created_at TEXT NOT NULL
        );
    """)
    con.commit(); con.close()

if not _use_supabase():
    _init_sqlite()

# ── Public DB API ─────────────────────────────────────────────────────────────

def log_generate(language: str):
    now = datetime.now(datetime.now().astimezone().tzinfo).isoformat()
    if _use_supabase():
        _get_supabase().table("stats").insert({"event":"generate","language":language,"created_at":now}).execute()
    else:
        con = _sqlite_conn()
        con.execute("INSERT INTO stats (event,language,created_at) VALUES (?,?,?)",("generate",language,datetime.now().isoformat()))
        con.commit(); con.close()

def insert_feedback(category, message, language, json_used):
    now = datetime.now().isoformat()
    if _use_supabase():
        _get_supabase().table("feedbacks").insert({
            "category":category,"message":message,"language":language,
            "json_used":json_used[:3000],"status":"Open","created_at":now
        }).execute()
    else:
        con = _sqlite_conn()
        con.execute("INSERT INTO feedbacks (category,message,language,json_used,created_at) VALUES (?,?,?,?,?)",
                    (category,message,language,json_used[:3000],now))
        con.commit(); con.close()

def fetch_feedbacks(status_filter="All", lang_filter="All"):
    if _use_supabase():
        sb = _get_supabase()
        q = sb.table("feedbacks").select("*").order("id",desc=True)
        if status_filter != "All": q = q.eq("status", status_filter)
        if lang_filter   != "All": q = q.eq("language", lang_filter)
        return q.execute().data or []
    else:
        con = _sqlite_conn()
        sql = "SELECT * FROM feedbacks WHERE 1=1"
        params = []
        if status_filter != "All": sql += " AND status=?";   params.append(status_filter)
        if lang_filter   != "All": sql += " AND language=?"; params.append(lang_filter)
        sql += " ORDER BY id DESC"
        rows = [dict(r) for r in con.execute(sql, params).fetchall()]
        con.close(); return rows

def update_feedback_status(fid, status):
    if _use_supabase():
        _get_supabase().table("feedbacks").update({"status":status}).eq("id",fid).execute()
    else:
        con = _sqlite_conn()
        con.execute("UPDATE feedbacks SET status=? WHERE id=?",(status,fid))
        con.commit(); con.close()

def delete_feedback(fid):
    if _use_supabase():
        _get_supabase().table("feedbacks").delete().eq("id",fid).execute()
    else:
        con = _sqlite_conn()
        con.execute("DELETE FROM feedbacks WHERE id=?",(fid,))
        con.commit(); con.close()

def get_stats():
    today = date.today().isoformat()
    if _use_supabase():
        sb = _get_supabase()
        def count(table, **filters):
            q = sb.table(table).select("*", count="exact")
            for k,v in filters.items(): q = q.eq(k,v)
            return q.execute().count or 0
        total_gen  = count("stats",  event="generate")
        scala_gen  = count("stats",  event="generate", language="Scala")
        py_gen     = count("stats",  event="generate", language="Python")
        today_gen  = len([r for r in sb.table("stats").select("created_at").eq("event","generate").execute().data
                          if (r.get("created_at") or "").startswith(today)])
        total_fb   = count("feedbacks")
        open_fb    = count("feedbacks", status="Open")
        resolved   = count("feedbacks", status="Resolved")
        wip        = count("feedbacks", status="In Progress")
        trend      = []
    else:
        con = _sqlite_conn()
        total_gen  = con.execute("SELECT COUNT(*) FROM stats WHERE event='generate'").fetchone()[0]
        today_gen  = con.execute("SELECT COUNT(*) FROM stats WHERE event='generate' AND created_at LIKE ?",(today+"%",)).fetchone()[0]
        scala_gen  = con.execute("SELECT COUNT(*) FROM stats WHERE event='generate' AND language='Scala'").fetchone()[0]
        py_gen     = con.execute("SELECT COUNT(*) FROM stats WHERE event='generate' AND language='Python'").fetchone()[0]
        total_fb   = con.execute("SELECT COUNT(*) FROM feedbacks").fetchone()[0]
        open_fb    = con.execute("SELECT COUNT(*) FROM feedbacks WHERE status='Open'").fetchone()[0]
        resolved   = con.execute("SELECT COUNT(*) FROM feedbacks WHERE status='Resolved'").fetchone()[0]
        wip        = con.execute("SELECT COUNT(*) FROM feedbacks WHERE status='In Progress'").fetchone()[0]
        trend_rows = con.execute("""SELECT DATE(created_at) as day, COUNT(*) as cnt
                                    FROM stats WHERE event='generate'
                                    GROUP BY day ORDER BY day DESC LIMIT 7""").fetchall()
        trend = [dict(r) for r in trend_rows]
        con.close()
    return dict(total_gen=total_gen, today_gen=today_gen, scala_gen=scala_gen, py_gen=py_gen,
                total_fb=total_fb, open_fb=open_fb, resolved=resolved, wip=wip, trend=trend)

def get_admin_passcode():
    try:    return st.secrets["ADMIN_PASSCODE"]
    except: return os.environ.get("ADMIN_PASSCODE","admin123")

# ─────────────────────────────────────────────────────────────────────────────
# Generator logic
# ─────────────────────────────────────────────────────────────────────────────

def to_class_name(s): return "".join(p.title() for p in s.split("_"))

def strip_comments(raw):
    hints, clean = {}, []
    for line in raw.splitlines():
        m = re.search(r'[#/]+\s*\[?ALLOWED VALUES[-:]?\s*([\w,\s]+)\]?', line, re.IGNORECASE)
        if m:
            vals = [v.strip() for v in m.group(1).split(",") if v.strip()]
            fm = re.search(r'"(\w+)"\s*:', line)
            if fm: hints[fm.group(1)] = vals
        line = re.sub(r'\s*#[^\n"]*$','',line)
        line = re.sub(r'\s*//[^\n"]*$','',line)
        clean.append(line)
    return "\n".join(clean), hints

def infer_scala_type(v):
    if isinstance(v,bool):  return "Boolean"
    if isinstance(v,int):   return "Long" if abs(v)>2_147_483_647 else "Int"
    if isinstance(v,float): return "Double"
    if isinstance(v,str):   return "String"
    if isinstance(v,list):  return f"Seq[{infer_scala_type(v[0])}]" if v else "Seq[Any]"
    if v is None:           return "Option[String]"
    return "String"

def infer_python_type(v):
    if isinstance(v,bool):  return "bool"
    if isinstance(v,int):   return "int"
    if isinstance(v,float): return "float"
    if isinstance(v,str):   return "str"
    if isinstance(v,list):  return f"list[{infer_python_type(v[0])}]" if v else "list"
    if v is None:           return "Optional[str]"
    return "str"

# ── Scala ─────────────────────────────────────────────────────────────────────

def scala_enum(fn, vals, pkg):
    cn=to_class_name(fn); L=[]
    if pkg: L.append(f"package {pkg}\n")
    L.append("import play.api.libs.json._\n")
    L.append(f"sealed trait {cn}\n\nobject {cn} {{")
    for v in vals: L.append(f"  case object {v} extends {cn}")
    L.append(f"\n  val values: Seq[{cn}] = Seq({', '.join(vals)})\n")
    L.append(f"  implicit val format: Format[{cn}] = new Format[{cn}] {{")
    L.append(f"    override def reads(json: JsValue): JsResult[{cn}] = json match {{")
    for v in vals: L.append(f'      case JsString("{v}") => JsSuccess({v})')
    L.append(f'      case JsString(s) => JsError(s"Unknown {cn}: $s. Allowed: {", ".join(vals)}")')
    L.append( '      case _           => JsError("Expected JSON string for '+cn+'")')
    L.append( '    }')
    L.append(f'    override def writes(t: {cn}): JsValue = JsString(t.toString)')
    L.append( '  }'); L.append('}')
    return {"filename":f"{cn}.scala","description":f"Sealed trait · {', '.join(vals)}","code":"\n".join(L)}

def scala_case_class(cn, fields, nested_map, pkg, enum_reg, option_fields=None):
    L=[]
    if pkg: L.append(f"package {pkg}\n")
    L.append("import play.api.libs.json._\n")
    option_fields = set(option_fields or [])
    fd=[]
    for fn,fv in fields.items():
        if fn in nested_map:
            base_ft = nested_map[fn]
            ft = f"Option[{base_ft}]" if fn in option_fields else base_ft
        elif fn in enum_reg:
            base_ft = enum_reg[fn]
            ft = f"Option[{base_ft}]" if fn in option_fields else base_ft
        elif fv is None:
            ft = "Option[String]"
        elif isinstance(fv, dict):
            base_ft = to_class_name(fn)
            ft = f"Option[{base_ft}]" if fn in option_fields else base_ft
        else:
            ft = infer_scala_type(fv)
            if fn in option_fields and not ft.startswith("Option["):
                ft = f"Option[{ft}]"
        fd.append((fn,ft))
    L.append(f"case class {cn}(")
    for i,(fn,ft) in enumerate(fd):
        L.append(f"  {fn}: {ft}{',' if i<len(fd)-1 else ''}")
    L.append(")\n")
    L.append(f"object {cn} {{")
    L.append(f"  implicit val FORMAT: Format[{cn}] = new Format[{cn}] {{\n")
    L.append(f"    override def reads(json: JsValue): JsResult[{cn}] = {{")
    L.append(f"      val result = {cn}(")
    for i,(fn,ft) in enumerate(fd):
        comma="," if i<len(fd)-1 else ""
        if ft.startswith("Option["):
            L.append(f'        {fn} = (json \\ "{fn}").asOpt[{ft[7:-1]}]{comma}')
        else:
            L.append(f'        {fn} = (json \\ "{fn}").as[{ft}]{comma}')
    L.append("      )\n      JsSuccess(result)\n    }\n")
    L.append(f"    override def writes(obj: {cn}): JsValue = Json.obj(")
    for i,(fn,ft) in enumerate(fd):
        L.append(f'      "{fn}" -> obj.{fn}{"," if i<len(fd)-1 else ""}')
    L.append("    )\n  }\n}")
    return {"filename":f"{cn}.scala","description":"Case class · explicit reads/writes","code":"\n".join(L)}

def generate_scala(raw, root, pkg, extra_enums, option_fields=None):
    clean, comment_enums = strip_comments(raw)
    data = json.loads(clean)
    all_enums = {**comment_enums, **extra_enums}
    files, enum_reg = [], {}
    for fn,vals in all_enums.items():
        cn=to_class_name(fn); enum_reg[fn]=cn
        files.append(scala_enum(fn,vals,pkg))
    def collect(obj, name):
        classes, nm = [], {}
        for k,v in obj.items():
            if isinstance(v,dict):
                nn=to_class_name(k); nm[k]=nn; classes.extend(collect(v,nn))
            elif isinstance(v,list) and v and isinstance(v[0],dict):
                nn=to_class_name(k); nm[k]=f"Seq[{nn}]"; classes.extend(collect(v[0],nn))
        classes.append((name,obj,nm)); return classes
    for cn,fields,nm in collect(data,root):
        files.append(scala_case_class(cn,fields,nm,pkg,enum_reg,option_fields))
    return files

# ── Python ────────────────────────────────────────────────────────────────────

def python_enum(fn, vals):
    cn=to_class_name(fn)
    L=["from enum import Enum","",f"class {cn}(str, Enum):"]
    for v in vals: L.append(f'    {v} = "{v}"')
    L+=["","    @classmethod",f'    def from_str(cls, value: str) -> "{cn}":',
        "        try:","            return cls(value)","        except ValueError:",
        "            allowed = ', '.join(e.value for e in cls)",
        f'            raise ValueError(f"Unknown {cn}: {{value}}. Allowed: {{allowed}}")']
    return {"filename":f"{cn}.py","description":f"Enum · {', '.join(vals)}","code":"\n".join(L)}

def python_dataclass(cn, fields, nested_map, enum_reg, option_fields=None):
    imports={"from dataclasses import dataclass, field","from typing import Optional, List"}
    for v in enum_reg.values(): imports.add(f"from {v} import {v}")
    for nn in set(nested_map.values()):
        base=nn.replace("List[","").replace("]",""); imports.add(f"from {base} import {base}")
    L=list(sorted(imports))+["","","@dataclass",f"class {cn}:"]
    option_fields = set(option_fields or [])
    fd=[]
    for fname,fv in fields.items():
        if fname in nested_map:
            base_ft = nested_map[fname]
            ft = f"Optional[{base_ft}]" if fname in option_fields else base_ft
        elif fname in enum_reg:
            base_ft = enum_reg[fname]
            ft = f"Optional[{base_ft}]" if fname in option_fields else base_ft
        elif fv is None:
            ft = "Optional[str]"
        elif isinstance(fv, dict):
            base_ft = to_class_name(fname)
            ft = f"Optional[{base_ft}]" if fname in option_fields else base_ft
        else:
            ft = infer_python_type(fv)
            if fname in option_fields and not ft.startswith("Optional["):
                ft = f"Optional[{ft}]"
        fd.append((fname,ft,fv))
        if "List" in ft or "list" in ft: L.append(f"    {fname}: {ft} = field(default_factory=list)")
        else:                            L.append(f"    {fname}: {ft} = None")
    L+=["","    @classmethod",f'    def from_dict(cls, data: dict) -> "{cn}":',
        "        return cls("]
    for i,(fname,ft,fv) in enumerate(fd):
        comma="," if i<len(fd)-1 else ""
        is_opt = ft.startswith("Optional[")
        if fname in enum_reg:
            ecn = enum_reg[fname]
            if is_opt:
                L.append(f'            {fname}={ecn}.from_str(data["{fname}"]) if data.get("{fname}") is not None else None{comma}')
            else:
                L.append(f'            {fname}={ecn}.from_str(data["{fname}"]){comma}')
        elif fname in nested_map:
            raw=nested_map[fname]
            if "List" in raw:
                inner=raw.replace("List[","").replace("]","")
                L.append(f'            {fname}=[{inner}.from_dict(i) for i in data.get("{fname}",[])]{comma}')
            elif is_opt:
                inner=ft[9:-1]
                L.append(f'            {fname}={inner}.from_dict(data["{fname}"]) if data.get("{fname}") is not None else None{comma}')
            else:
                L.append(f'            {fname}={raw}.from_dict(data["{fname}"]){comma}')
        else:
            L.append(f'            {fname}=data.get("{fname}"){comma}')
    L+=["        )","","    def to_dict(self) -> dict:","        result = {}"]
    for fname,ft,fv in fd:
        if fname in enum_reg:
            L.append(f'        if self.{fname}: result["{fname}"] = self.{fname}.value')
        elif fname in nested_map:
            raw=nested_map[fname]
            if "List" in raw: L.append(f'        result["{fname}"] = [i.to_dict() for i in self.{fname}]')
            else:             L.append(f'        if self.{fname}: result["{fname}"] = self.{fname}.to_dict()')
        else: L.append(f'        result["{fname}"] = self.{fname}')
    L.append("        return result")
    return {"filename":f"{cn}.py","description":"Dataclass · from_dict/to_dict","code":"\n".join(L)}

def generate_python(raw, root, extra_enums, option_fields=None):
    clean, comment_enums = strip_comments(raw)
    data = json.loads(clean)
    all_enums = {**comment_enums, **extra_enums}
    files, enum_reg = [], {}
    for fn,vals in all_enums.items():
        cn=to_class_name(fn); enum_reg[fn]=cn
        files.append(python_enum(fn,vals))
    def collect(obj, name):
        classes, nm = [], {}
        for k,v in obj.items():
            if isinstance(v,dict):
                nn=to_class_name(k); nm[k]=nn; classes.extend(collect(v,nn))
            elif isinstance(v,list) and v and isinstance(v[0],dict):
                nn=to_class_name(k); nm[k]=f"List[{nn}]"; classes.extend(collect(v[0],nn))
        classes.append((name,obj,nm)); return classes
    for cn,fields,nm in collect(data,root):
        files.append(python_dataclass(cn,fields,nm,enum_reg,option_fields))
    return files

def build_zip(files, root_name):
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as zf:
        for f in files: zf.writestr(f["filename"],f["code"])
    return buf.getvalue()

# ─────────────────────────────────────────────────────────────────────────────
# Session defaults
# ─────────────────────────────────────────────────────────────────────────────

for k,v in {"language":"Scala","admin_auth":False,"active_tab":"generator"}.items():
    if k not in st.session_state: st.session_state[k]=v

# ─────────────────────────────────────────────────────────────────────────────
# Header + nav
# ─────────────────────────────────────────────────────────────────────────────

h1,h2,h3 = st.columns([3,1,1])
with h1:
    st.markdown("## 🎯 CodeCast")
    st.caption("Generate Scala case classes or Python dataclasses from any JSON sample — instantly")
with h2:
    if st.button("🛠 Generator", use_container_width=True,
                 type="primary" if st.session_state["active_tab"]=="generator" else "secondary"):
        st.session_state["active_tab"]="generator"; st.rerun()
with h3:
    if st.button("🔒 Admin", use_container_width=True,
                 type="primary" if st.session_state["active_tab"]=="admin" else "secondary"):
        st.session_state["active_tab"]="admin"; st.rerun()

st.markdown("---")

SAMPLE_JSON="""{
  "config_id": "1373587000007063007",
  "quota_id": "1373587000007063011",
  "client_details": {
    "notify_callback_url": "https://crmlab19.localzoho.com/crm/forecast/notify"
  },
  "trigger_type": "CREATE"
}"""

# ─────────────────────────────────────────────────────────────────────────────
# GENERATOR TAB
# ─────────────────────────────────────────────────────────────────────────────

if st.session_state["active_tab"] == "generator":

    with st.sidebar:
        st.header("⚙️ Configuration")
        st.subheader("🌐 Target Language")
        lang_choice = st.radio("lang",["Scala","Python"],
                               index=0 if st.session_state["language"]=="Scala" else 1,
                               horizontal=True, label_visibility="collapsed")
        if lang_choice != st.session_state["language"]:
            st.session_state["language"]=lang_choice
            st.session_state.pop("files",None); st.rerun()
        lang=st.session_state["language"]
        st.markdown("---")
        root_class   = st.text_input("Root class name *", value="",
                                     placeholder="e.g. MyRequest  (required)")
        package_name = st.text_input("Package name (optional)", value="",
                                     placeholder="e.g. com.example.myapp") if lang=="Scala" else ""
        st.markdown("---")
        st.subheader("🔤 Enum Fields")
        st.caption("One per line — `fieldName: VAL1,VAL2`")
        enum_raw = st.text_area("Enums", value="",
                                placeholder="e.g.\ntrigger_type: CREATE,UPDATE,DELETE",
                                height=110, label_visibility="collapsed")
        st.markdown("---")
        st.subheader("🔲 Option Fields")
        st.caption("Field names to mark as optional — one per line")
        option_raw = st.text_area("Options", value="",
                                  placeholder="e.g.\nquota_id\nclient_details",
                                  height=90, label_visibility="collapsed")
        st.markdown("---")
        db_mode = "☁️ Supabase" if _use_supabase() else "💾 Local SQLite"
        st.caption(f"Storage: {db_mode}")

    col_in, col_out, col_fb = st.columns([1, 1.1, 0.8], gap="large")

    with col_in:
        lang=st.session_state["language"]
        st.subheader(f"📥 JSON Input  →  {lang}")
        json_input = st.text_area("JSON", value=SAMPLE_JSON, height=340, label_visibility="collapsed")
        if st.button(f"⚡ Generate {lang} Code", type="primary", use_container_width=True):
            extra_enums={}
            for line in enum_raw.strip().splitlines():
                if ":" in line:
                    fn,vs=line.split(":",1)
                    vals=[v.strip() for v in vs.split(",") if v.strip()]
                    if fn.strip() and vals: extra_enums[fn.strip()]=vals
            option_fields=[f.strip() for f in option_raw.strip().splitlines() if f.strip()]
            try:
                cl,_=strip_comments(json_input); json.loads(cl)
            except json.JSONDecodeError as e:
                st.error(f"❌ Invalid JSON: {e}"); st.stop()
            if not root_class.strip():
                st.error("❌ Root class name is required."); st.stop()
            try:
                if lang=="Scala":
                    files=generate_scala(json_input, root_class.strip(), package_name.strip(), extra_enums, option_fields)
                else:
                    files=generate_python(json_input, root_class.strip(), extra_enums, option_fields)
                st.session_state["files"]=files
                st.session_state["json_used"]=json_input
                log_generate(lang)
                st.success(f"✅ Generated {len(files)} {lang} file(s)")
            except Exception as e:
                st.error(f"❌ {e}"); st.stop()

    with col_out:
        st.subheader("📤 Generated Files")
        if "files" in st.session_state:
            files=st.session_state["files"]
            hl="scala" if st.session_state["language"]=="Scala" else "python"
            tabs=st.tabs([f["filename"] for f in files])
            for tab,f in zip(tabs,files):
                with tab:
                    st.caption(f.get("description",""))
                    st.code(f["code"], language=hl)
                    st.download_button(f"⬇️ {f['filename']}", f["code"],
                                       file_name=f["filename"], mime="text/plain",
                                       key=f"dl_{f['filename']}", use_container_width=True)
            st.markdown("---")
            ext="scala" if st.session_state["language"]=="Scala" else "python"
            st.download_button(
                f"⬇️ Download ALL  ({root_class.strip()}_{ext}.zip)",
                build_zip(files, root_class.strip()),
                file_name=f"{root_class.strip()}_{ext}.zip",
                mime="application/zip", use_container_width=True)
        else:
            st.info("Generated files appear here after you click **Generate**.")

    with col_fb:
        st.subheader("💬 Feedback")
        fb_cat = st.selectbox("Category",
                              ["Incorrect output","Missing feature","Wrong type inference",
                               "Enum not detected","Other"],
                              label_visibility="collapsed")
        fb_msg = st.text_area("Message",
                              placeholder="e.g. nested arrays not handled correctly…",
                              height=180, label_visibility="collapsed")
        if st.button("Submit Feedback", use_container_width=True):
            if not fb_msg.strip():
                st.warning("Please describe your feedback before submitting.")
            else:
                insert_feedback(fb_cat, fb_msg.strip(),
                                st.session_state.get("language",""),
                                st.session_state.get("json_used",""))
                st.success("✅ Feedback submitted — thank you!")
                st.caption("Your input helps improve CodeCast.")

# ─────────────────────────────────────────────────────────────────────────────
# ADMIN TAB
# ─────────────────────────────────────────────────────────────────────────────

elif st.session_state["active_tab"] == "admin":

    if not st.session_state["admin_auth"]:
        st.subheader("🔒 Admin Login")
        pc = st.text_input("Passcode", type="password", placeholder="Enter admin passcode")
        if st.button("Unlock", type="primary"):
            if pc == get_admin_passcode():
                st.session_state["admin_auth"]=True; st.rerun()
            else:
                st.error("❌ Incorrect passcode.")
        st.stop()

    lc,_ = st.columns([1,6])
    with lc:
        if st.button("🔓 Lock"): st.session_state["admin_auth"]=False; st.rerun()

    st.subheader("🛡 Admin Dashboard")

    s = get_stats()

    st.markdown("#### ⚡ Generation Stats")
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Total Generates",  s["total_gen"])
    c2.metric("Today",            s["today_gen"])
    c3.metric("Scala",            s["scala_gen"])
    c4.metric("Python",           s["py_gen"])

    st.markdown("#### 💬 Feedback Stats")
    f1,f2,f3,f4 = st.columns(4)
    f1.metric("Total",            s["total_fb"])
    f2.metric("🔴 Open",          s["open_fb"])
    f3.metric("🟡 In Progress",   s["wip"])
    f4.metric("🟢 Resolved",      s["resolved"])

    if s.get("trend"):
        st.markdown("#### 📅 Last 7 Days")
        trend_cols = st.columns(len(s["trend"]))
        for col, row in zip(trend_cols, s["trend"]):
            col.metric(row["day"][-5:], row["cnt"])

    st.markdown("---")
    st.markdown("#### 📋 Feedback Records")
    fc1,fc2 = st.columns(2)
    with fc1: flt_status = st.selectbox("Status",["All","Open","In Progress","Resolved"])
    with fc2: flt_lang   = st.selectbox("Language",["All","Scala","Python"])

    rows = fetch_feedbacks(flt_status, flt_lang)
    st.markdown(f"**{len(rows)} record(s)**")

    if not rows:
        st.info("No feedback matches the selected filters.")
    else:
        icons={"Open":"🔴","In Progress":"🟡","Resolved":"🟢"}
        for row in rows:
            fid=row["id"]; icon=icons.get(row["status"],"⚪")
            lang_tag=row.get("language","") or ""
            created=(row.get("created_at","") or "")[:16]
            with st.expander(f"{icon}  [{fid}]  {row['category']}  ·  {lang_tag}  ·  {created}"):
                st.markdown(f"**Message:** {row['message']}")
                if row.get("json_used"):
                    with st.expander("JSON snapshot"):
                        st.code(row["json_used"][:1500], language="json")
                a1,a2,a3 = st.columns(3)
                with a1:
                    if st.button("✅ Resolved",    key=f"res_{fid}"):
                        update_feedback_status(fid,"Resolved");    st.rerun()
                with a2:
                    if st.button("🔄 In Progress", key=f"wip_{fid}"):
                        update_feedback_status(fid,"In Progress"); st.rerun()
                with a3:
                    if st.button("🗑 Delete",       key=f"del_{fid}"):
                        delete_feedback(fid); st.rerun()

    st.markdown("---")
    import csv, io as sio
    all_rows = fetch_feedbacks()
    if all_rows:
        buf = sio.StringIO()
        # auto-detect fieldnames from actual data — works with both SQLite and Supabase
        fieldnames = list(all_rows[0].keys())
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
        st.download_button("⬇️ Export CSV", buf.getvalue(),
                           file_name="codecast_feedbacks.csv", mime="text/csv")
    else:
        st.caption("No feedback to export yet.")

# ─────────────────────────────────────────────────────────────────────────────
# Footer
# ─────────────────────────────────────────────────────────────────────────────

st.markdown("---")
st.markdown("""
    <div style="text-align:center;color:gray;font-size:13px;padding:6px 0">
        For custom requirements, feature requests or incorrect output — reach out directly:<br>
        <a href="mailto:dinesh.jr@zohocorp.com" style="color:#4F8BF9;text-decoration:none;">
            📧 dinesh.jr@zohocorp.com
        </a>
    </div>""", unsafe_allow_html=True)
