"""Interactive catalogue of the configured server's OpenAPI operations, using the app's TLS and token."""

from __future__ import annotations

import json
import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from urllib.parse import quote, urlencode

from ..server.client import ServerError, UPLOAD_NAME, XLSX_MEDIA_TYPE, _multipart
from . import theme

METHODS = {"get", "post", "put", "patch", "delete"}
GROUP = {
    "process": "프롬프트",
    "files": "파일",
    "events": "사유",
    "logs": "로그",
    "cases": "검토 건",
    "false-positives": "오탐",
    "policy": "정책",
    "auth": "인증",
    "health": "상태",
}


def schema_example(spec, schema, seen=()):
    ref = schema.get("$ref")
    if ref:
        if ref in seen:
            return None
        return schema_example(spec, spec["components"]["schemas"][ref.split("/")[-1]], seen + (ref,))
    for key in ("example", "default", "const"):
        if key in schema:
            return schema[key]
    if schema.get("enum"):
        return schema["enum"][0]
    variants = schema.get("anyOf") or schema.get("oneOf") or schema.get("allOf")
    if variants:
        return schema_example(spec, variants[0], seen)
    if "properties" in schema:
        return {
            k: schema_example(spec, v, seen)
            for k, v in schema["properties"].items()
            if k in schema.get("required", [])
        }
    return {"string": "", "number": 0, "integer": 0, "array": [], "boolean": False, "object": {}}.get(
        schema.get("type")
    )


def prepare(path, op, fields):
    if not isinstance(fields, dict):
        raise ValueError("매개변수에는 JSON 객체가 필요합니다.")
    query, headers = {}, {"Accept": "*/*"}
    for param in op.get("parameters", []):
        name = param["name"]
        val = fields.get(name)
        if val is None or val == "":
            if param.get("required"):
                raise ValueError(name + " 값을 입력해 주세요.")
            continue
        if param["in"] == "path":
            if str(val) in {".", ".."}:
                raise ValueError("경로 값을 확인해 주세요.")
            path = path.replace("{" + name + "}", quote(str(val), safe=""))
        elif param["in"] == "query":
            query[name] = val
        elif param["in"] == "header" and name.lower() in {"if-match", "if-none-match"}:
            headers[name] = str(val)
    if not path.startswith("/") or path.startswith("//") or re.search(r"[{}\r\n?#]", path):
        raise ValueError("요청 경로를 확인해 주세요.")
    return path + ("?" + urlencode(query, doseq=True) if query else ""), headers


class ServerToolsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.server = app.server
        self.responses = queue.Queue()
        self.file = None
        self.download = None
        theme.window(self, "팀 서버 기능", 790, 680, 620, 460)
        self.choice = ttk.Combobox(self, state="readonly")
        self.choice.pack(fill="x", padx=12, pady=8)
        self.choice.bind("<<ComboboxSelected>>", lambda _: self.select())
        self.detail = ttk.Label(self, wraplength=690)
        self.detail.pack(anchor="w", padx=12)

        def field(title, height):
            ttk.Label(self, text=title).pack(anchor="w", padx=12, pady=(6, 0))
            box = tk.Text(self, height=height, wrap="word")
            box.pack(fill="both", expand=title == "응답", padx=12)
            return box

        self.params, self.body = field("경로·쿼리·조건 헤더 (JSON)", 3), field("요청 본문 (JSON)", 5)
        row = ttk.Frame(self)
        row.pack(fill="x", padx=12, pady=6)
        self.pick = ttk.Button(row, text="엑셀 선택", command=self.pick_file)
        self.pick.pack(side="left")
        self.submit = ttk.Button(row, text="선택한 기능 실행", command=self.run, state="disabled")
        self.submit.pack(side="left")
        self.save = ttk.Button(row, text="응답 파일 저장", command=self.save_file, state="disabled")
        self.save.pack(side="right")
        self.output = field("응답", 7)
        self.after(80, self.poll)
        self.request("schema", lambda: self.server._request("GET", "/openapi.json"))

    def request(self, kind, call):
        def worker():
            try:
                self.responses.put((kind, call()))
            except ServerError as exc:
                self.responses.put(("error", exc.message))
            except Exception:
                self.responses.put(("error", "팀 서버 요청을 처리할 수 없습니다."))

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def write(box, value):
        box.delete("1.0", "end")
        box.insert("1.0", value)

    def poll(self):
        try:
            kind, value = self.responses.get_nowait()
        except queue.Empty:
            self.after(80, self.poll)
            return
        if kind == "schema":
            self.spec = value
            self.operations = {}
            for path, item in value.get("paths", {}).items():
                for method, op in item.items():
                    if method in METHODS:
                        group = GROUP.get(path.split("/")[1], "서버")
                        label = f"{group}   {method.upper()}  {path}"
                        self.operations[label] = (
                            method.upper(),
                            path,
                            {**op, "parameters": item.get("parameters", []) + op.get("parameters", [])},
                        )
            self.choice["values"] = sorted(self.operations)
            if self.operations:
                self.choice.current(0)
                self.select()
        elif kind == "response":
            code, headers, raw = value
            mime = headers.get("Content-Type", "")
            if "json" in mime:
                output = json.dumps(json.loads(raw or b"null"), ensure_ascii=False, indent=2)
            elif "text/" in mime or not raw:
                output = raw.decode("utf-8", "replace")
            else:
                self.download = raw
                self.save.configure(state="normal")
                output = f"받은 파일 {len(raw):,}바이트"
            self.write(self.output, f"HTTP {code}   {headers.get('ETag', '')}\n{output}")
        else:
            self.write(self.output, value)
        if self.choice.get():
            self.choice.configure(state="readonly")
            self.submit.configure(state="normal")
        self.after(80, self.poll)

    def select(self):
        _, _, op = self.operations[self.choice.get()]
        self.file = self.download = None
        self.save.configure(state="disabled")
        self.detail.configure(text=op.get("summary", ""))
        params = {p["name"]: p.get("schema", {}).get("default") for p in op["parameters"]}
        self.write(self.params, json.dumps(params, ensure_ascii=False, indent=2))
        content = op.get("requestBody", {}).get("content", {})
        schema = content.get("application/json", {}).get("schema")
        self.write(
            self.body,
            json.dumps(schema_example(self.spec, schema), ensure_ascii=False, indent=2) if schema else "",
        )
        self.pick.configure(state="normal" if "multipart/form-data" in content else "disabled")

    def pick_file(self):
        self.file = filedialog.askopenfilename(parent=self, filetypes=[("Excel", "*.xlsx")]) or None

    def save_file(self):
        if self.download is not None:
            path = filedialog.asksaveasfilename(parent=self, initialfile="보호본.xlsx")
            if path:
                Path(path).write_bytes(self.download)

    def run(self):
        method, path, op = self.operations[self.choice.get()]
        try:
            path, headers = prepare(path, op, json.loads(self.params.get("1.0", "end")))
            content = op.get("requestBody", {}).get("content", {})
            body = None
            if "multipart/form-data" in content:
                if not self.file or Path(self.file).stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("8MB 이하의 엑셀 파일을 선택해 주세요.")
                body, headers["Content-Type"] = _multipart(
                    "file", UPLOAD_NAME, Path(self.file).read_bytes(), XLSX_MEDIA_TYPE
                )
            elif "application/json" in content:
                body = json.dumps(json.loads(self.body.get("1.0", "end")), ensure_ascii=False).encode()
                headers["Content-Type"] = "application/json"
        except (ValueError, OSError) as exc:
            self.write(self.output, str(exc))
            return
        self.download = None
        self.save.configure(state="disabled")
        self.submit.configure(state="disabled")
        self.choice.configure(state="disabled")
        self.write(self.output, "실행 중…")
        self.request("response", lambda: self.server._send(method, path, body, headers))
