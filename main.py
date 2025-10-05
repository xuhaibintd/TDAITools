from fastapi import FastAPI, Request, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
import json, io
from openpyxl import Workbook



app = FastAPI()

# Mount static directory
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/convert")
async def convert(file: UploadFile):
    content = await file.read()
    data = json.loads(content)

    # ===== Core mapping from agent JSON to the 13 customer columns =====
    nodes = data.get("logic", {}).get("agent_sdk", {}).get("nodes", [])
    edges = data.get("logic", {}).get("agent_sdk", {}).get("edges", [])
    default_model = data.get("logic", {}).get("model", "-")
    default_temp = data.get("logic", {}).get("temperature", "-")

    # Edge relationships
    outs, ins, rules = {}, {}, {}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        r = (e.get("data") or {}).get("handoffRule", "-")
        if s and t:
            outs.setdefault(s, []).append(t)
            ins.setdefault(t, []).append(s)
            rules[(s, t)] = r

    # Build Excel workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "ノード一覧"

    headers = ["ブロック名","ブロックID","知識設定内容","ワークフロー設定内容",
               "メモリ設定内容","LLM設定内容","ツール設定内容","ガードレール設定内容",
               "プロンプト設定内容","出力設定内容","ハンドオフルール","ハンドオフ先","ハンドオフ元"]
    ws.append(headers)

    def schema_keys(ot):
        if not isinstance(ot, dict):
            return []
        js = ot.get("json_schema")
        if isinstance(js, dict) and isinstance(js.get("properties"), dict):
            return list(js["properties"].keys())
        return []

    def pick_config(node_data, keys):
        for key in keys:
            value = node_data.get(key) if isinstance(node_data, dict) else None
            if value not in (None, "", [], {}):
                return value
        return None

    def format_config_value(value):
        if value in (None, "", [], {}):
            return "-"
        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else "-"
        if isinstance(value, (list, tuple, set)):
            filtered = [str(item).strip() for item in value if str(item).strip()]
            return ", ".join(filtered) if filtered else "-"
        if isinstance(value, dict):
            cleaned = {k: v for k, v in value.items() if v not in (None, "", [], {})}
            if not cleaned:
                return "-"
            return json.dumps(cleaned, ensure_ascii=False)
        return str(value)

    type_labels = {"conversation": "Conversation", "tool_call": "Tool Call"}

    for node in nodes:
        d = node.get("data", {})
        nid = node.get("id")
        step = d.get("step") or nid
        raw_type = d.get("type") or "-"
        ntype = raw_type.lower() if isinstance(raw_type, str) else str(raw_type)
        model = d.get("model") or default_model
        temperature = d.get("temperature", default_temp)
        llm_parts = []
        if model not in (None, "", "-"):
            llm_parts.append(f"model={model}")
        if temperature not in (None, "", "-"):
            llm_parts.append(f"temperature={temperature}")
        llm = ", ".join(llm_parts) if llm_parts else "-"

        tool = "-"
        if ntype == "tool_call":
            m = d.get("toolCallData", {}).get("method", "").upper()
            u = d.get("toolCallData", {}).get("requestURL", "")
            tool = f"{m} {u}".strip() or "-"

        instr = (d.get("instruction") or "-")[:200]
        out_type = d.get("output_type", {})
        output_keys = schema_keys(out_type)
        if out_type.get("type") == "plain_text":
            out_text = "plain_text"
        elif output_keys:
            out_text = f"{out_type.get('type','-')}: {{{', '.join(output_keys)}}}"
        else:
            out_text = out_type.get("type") or "-"

        mem_text = f"contextに{', '.join(output_keys)}を保存" if output_keys else "-"

        knowledge_config = format_config_value(pick_config(d, ["knowledgeSettings", "knowledge_settings", "knowledge_config", "knowledge"]))
        guardrail_config = format_config_value(pick_config(d, ["guardrailSettings", "guardrail_settings", "guardrail_config", "guardrail"]))
        workflow_raw = pick_config(d, ["workflowSettings", "workflow_settings", "workflow"])
        workflow_config = format_config_value(workflow_raw) if workflow_raw is not None else "-"
        type_label = type_labels.get(ntype, raw_type)
        if workflow_config == "-":
            workflow_config = type_label

        to_nodes = outs.get(nid, [])
        from_nodes = ins.get(nid, [])
        rule_str = " / ".join([rules.get((nid, t), "-") for t in to_nodes]) or "-"
        to_str = " / ".join(to_nodes) or "-"
        from_str = " / ".join(from_nodes) or "-"

        row = [
            step, nid,
            knowledge_config,
            workflow_config,
            mem_text,
            llm,
            tool,
            guardrail_config,
            instr,
            out_text,
            rule_str,
            to_str,
            from_str
        ]
        ws.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=converted.xlsx"}
    )
