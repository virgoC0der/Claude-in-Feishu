#!/usr/bin/env python3
"""
飞书文档操作工具
用法: python3 feishu_docs.py <command> [args...]

Commands:
  token                                    获取 tenant_access_token
  read        <doc_id>                     读取文档内容（纯文本）
  create      <title> [folder_token]       新建文档
  append      <doc_id> <text>              在文档末尾追加文本
  mkdir       <name> [folder_token]        新建文件夹
  list        [folder_token]               列出云盘文件（默认根目录）
  search      <query>                      搜索文档
  move        <file_token> <folder_token>  移动文件到指定文件夹
  folders                                  列出所有文件夹
  organize                                 智能整理文档（分析内容后分类）
  cal_list                                 列出日历
  event_create <summary> <start> <end> [description]
                                           创建日历事件（时间格式：2026-03-16T10:00:00+08:00，自动邀请 Vanessa）
  event_list  [calendar_id]               列出日历事件（默认主日历）
  send_image  <chat_id> <image_path>       发送图片到飞书对话
  send_file   <chat_id> <file_path>        发送文件到飞书对话
"""

import sys
import json
import os
import urllib.request
import urllib.parse

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
BASE = os.environ.get("FEISHU_DOMAIN", "https://open.feishu.cn")
USER_TOKEN_FILE = os.path.expanduser("~/.claude-autopilot/feishu_user_token.json")
SHARED_CALENDAR_ID = os.environ.get("FEISHU_CALENDAR_ID", "")
VANESSA_OPEN_ID = os.environ.get("FEISHU_USER_OPEN_ID", "")


def call(method, path, body=None, token=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def get_token():
    """Get tenant_access_token (for calendar/messaging operations)"""
    resp = call("POST", "/open-apis/auth/v3/tenant_access_token/internal",
                {"app_id": APP_ID, "app_secret": APP_SECRET})
    if resp.get("code") != 0:
        raise RuntimeError(f"Token error: {resp}")
    return resp["tenant_access_token"]


def get_user_token():
    """Get user_access_token for personal drive operations, auto-refresh if needed"""
    import time
    if not os.path.exists(USER_TOKEN_FILE):
        raise RuntimeError("No user token. Run: python3 feishu_oauth.py")
    data = json.load(open(USER_TOKEN_FILE))
    elapsed = int(time.time()) - data["saved_at"]
    if elapsed < data["expires_in"] - 300:
        return data["access_token"]
    # Refresh
    app_resp = call("POST", "/open-apis/auth/v3/app_access_token/internal",
                    {"app_id": APP_ID, "app_secret": APP_SECRET})
    app_token = app_resp["app_access_token"]
    resp = call("POST", "/open-apis/authen/v1/oidc/refresh_access_token",
                {"grant_type": "refresh_token", "refresh_token": data["refresh_token"]},
                token=app_token)
    if resp.get("code") != 0:
        raise RuntimeError(f"Refresh error: {resp}")
    new_data = {**resp["data"], "saved_at": int(time.time())}
    json.dump(new_data, open(USER_TOKEN_FILE, "w"), indent=2)
    return new_data["access_token"]


def upload_image(image_path, token):
    """Upload image to Feishu and return image_key"""
    import mimetypes
    boundary = "----PythonFormBoundary7MA4YWxkTrZu0gW"
    filename = os.path.basename(image_path)
    mime_type = mimetypes.guess_type(image_path)[0] or "image/png"

    with open(image_path, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image_type"\r\n\r\nmessage\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{BASE}/open-apis/im/v1/images",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        resp = json.loads(r.read())
    if resp.get("code") != 0:
        raise RuntimeError(f"Upload image error: {resp}")
    return resp["data"]["image_key"]


def upload_file(file_path, token):
    """Upload file to Feishu and return file_key"""
    import mimetypes
    boundary = "----PythonFormBoundary7MA4YWxkTrZu0gW"
    filename = os.path.basename(file_path)
    mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

    # Determine file_type for Feishu API
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".xls", ".xlsx", ".csv"):
        file_type = "xls"
    elif ext in (".ppt", ".pptx"):
        file_type = "ppt"
    elif ext == ".pdf":
        file_type = "pdf"
    else:
        file_type = "stream"

    with open(file_path, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file_type"\r\n\r\n{file_type}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file_name"\r\n\r\n{filename}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{BASE}/open-apis/im/v1/files",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        resp = json.loads(r.read())
    if resp.get("code") != 0:
        raise RuntimeError(f"Upload file error: {resp}")
    return resp["data"]["file_key"]


def send_image_to_chat(chat_id, image_path):
    """Upload image and send to a Feishu chat"""
    t = get_token()
    image_key = upload_image(image_path, t)
    msg = {"receive_id": chat_id, "msg_type": "image", "content": json.dumps({"image_key": image_key})}
    resp = call("POST", "/open-apis/im/v1/messages?receive_id_type=chat_id", msg, token=t)
    if resp.get("code") != 0:
        raise RuntimeError(f"Send image error: {resp}")
    return {"image_key": image_key, "message_id": resp["data"]["message_id"]}


def send_file_to_chat(chat_id, file_path):
    """Upload file and send to a Feishu chat"""
    t = get_token()
    file_key = upload_file(file_path, t)
    msg = {"receive_id": chat_id, "msg_type": "file", "content": json.dumps({"file_key": file_key})}
    resp = call("POST", "/open-apis/im/v1/messages?receive_id_type=chat_id", msg, token=t)
    if resp.get("code") != 0:
        raise RuntimeError(f"Send file error: {resp}")
    return {"file_key": file_key, "message_id": resp["data"]["message_id"]}


def read_doc(doc_id):
    t = get_token()
    # Get document blocks
    resp = call("GET", f"/open-apis/docx/v1/documents/{doc_id}/blocks?page_size=500", token=t)
    if resp.get("code") != 0:
        raise RuntimeError(f"Read error: {resp}")

    lines = []
    for block in resp.get("data", {}).get("items", []):
        bt = block.get("block_type")
        # Paragraph / heading types
        for key in ["text", "heading1", "heading2", "heading3", "heading4",
                    "heading5", "heading6", "heading7", "heading8", "heading9"]:
            content = block.get(key, {})
            if content:
                parts = [e.get("content", "") for e in content.get("elements", [])]
                line = "".join(parts)
                if key.startswith("heading"):
                    level = int(key[-1])
                    line = "#" * level + " " + line
                lines.append(line)
    return "\n".join(lines)


def create_doc(title, folder_token=None):
    t = get_user_token()
    body = {"title": title}
    if folder_token:
        body["folder_token"] = folder_token
    resp = call("POST", "/open-apis/docx/v1/documents", body, token=t)
    if resp.get("code") != 0:
        raise RuntimeError(f"Create error: {resp}")
    doc = resp["data"]["document"]
    return {"document_id": doc["document_id"], "url": f"https://docs.feishu.cn/docx/{doc['document_id']}"}


def append_text(doc_id, text):
    t = get_token()
    # Get doc to find last block
    resp = call("GET", f"/open-apis/docx/v1/documents/{doc_id}", token=t)
    if resp.get("code") != 0:
        raise RuntimeError(f"Get doc error: {resp}")

    doc_block_id = resp["data"]["document"]["document_id"]

    # Build paragraph blocks for each line
    blocks = []
    for line in text.split("\n"):
        blocks.append({
            "block_type": 2,  # paragraph
            "text": {
                "elements": [{"text_run": {"content": line, "text_element_style": {}}}],
                "style": {}
            }
        })

    resp = call("POST", f"/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_block_id}/children",
                {"children": blocks, "index": -1}, token=t)
    if resp.get("code") != 0:
        raise RuntimeError(f"Append error: {resp}")
    return f"Appended {len(blocks)} block(s) to {doc_id}"


def get_root_folder_token():
    t = get_user_token()
    resp = call("GET", "/open-apis/drive/explorer/v2/root_folder/meta", token=t)
    if resp.get("code") != 0:
        raise RuntimeError(f"Root folder error: {resp}")
    return resp["data"]["token"]


def create_folder(name, folder_token=None):
    t = get_user_token()
    if not folder_token:
        folder_token = get_root_folder_token()
    resp = call("POST", "/open-apis/drive/v1/files/create_folder",
                {"name": name, "folder_token": folder_token}, token=t)
    if resp.get("code") != 0:
        raise RuntimeError(f"Create folder error: {resp}")
    f = resp["data"]
    return {"token": f["token"], "url": f["url"]}


def list_files(folder_token=None):
    t = get_user_token()
    path = "/open-apis/drive/v1/files?page_size=50"
    if folder_token:
        path += f"&folder_token={folder_token}"
    resp = call("GET", path, token=t)
    if resp.get("code") != 0:
        raise RuntimeError(f"List error: {resp}")
    files = resp.get("data", {}).get("files", [])
    return [{"name": f["name"], "type": f["type"], "token": f["token"]} for f in files]


def get_primary_calendar_id():
    t = get_token()
    resp = call("GET", "/open-apis/calendar/v4/calendars", token=t)
    if resp.get("code") != 0:
        raise RuntimeError(f"Calendar list error: {resp}")
    for cal in resp.get("data", {}).get("calendar_list", []):
        if cal.get("type") == "primary":
            return cal["calendar_id"]
    raise RuntimeError("Primary calendar not found")


def list_calendars():
    t = get_token()
    resp = call("GET", "/open-apis/calendar/v4/calendars", token=t)
    if resp.get("code") != 0:
        raise RuntimeError(f"Calendar list error: {resp}")
    cals = resp.get("data", {}).get("calendar_list", [])
    return [{"id": c["calendar_id"], "name": c["summary"], "type": c["type"]} for c in cals]


def create_event(summary, start_time, end_time, description=None, calendar_id=None):
    """start_time/end_time: ISO格式 2026-03-17T10:00:00+08:00 或 Unix timestamp 字符串"""
    import datetime as dt
    t = get_token()
    if not calendar_id:
        calendar_id = SHARED_CALENDAR_ID  # Use shared calendar so Vanessa can see events

    def to_timestamp(s):
        if s.isdigit():
            return s
        # Parse ISO format to unix timestamp
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
            try:
                d = dt.datetime.strptime(s, fmt)
                if d.tzinfo is None:
                    import zoneinfo
                    d = d.replace(tzinfo=zoneinfo.ZoneInfo("Asia/Shanghai"))
                return str(int(d.timestamp()))
            except ValueError:
                continue
        raise ValueError(f"Cannot parse time: {s}")

    body = {
        "summary": summary,
        "start_time": {"timestamp": to_timestamp(start_time), "timezone": "Asia/Shanghai"},
        "end_time":   {"timestamp": to_timestamp(end_time),   "timezone": "Asia/Shanghai"},
    }
    if description:
        body["description"] = description
    resp = call("POST", f"/open-apis/calendar/v4/calendars/{urllib.parse.quote(calendar_id, safe='')}/events",
                body, token=t)
    if resp.get("code") != 0:
        raise RuntimeError(f"Event create error: {resp}")
    event = resp["data"]["event"]
    event_id = event["event_id"]

    return {"event_id": event_id, "summary": event["summary"],
            "start": event["start_time"], "end": event["end_time"]}


def list_events(calendar_id=None):
    t = get_token()
    if not calendar_id:
        calendar_id = SHARED_CALENDAR_ID
    resp = call("GET", f"/open-apis/calendar/v4/calendars/{urllib.parse.quote(calendar_id, safe='')}/events?page_size=50",
                token=t)
    if resp.get("code") != 0:
        raise RuntimeError(f"Event list error: {resp}")
    items = resp.get("data", {}).get("items", [])
    return [{"event_id": e["event_id"], "summary": e.get("summary", ""),
             "start": e.get("start_time", {}), "end": e.get("end_time", {})} for e in items]


def search_docs(query):
    t = get_token()
    resp = call("POST", "/open-apis/suite/docs-api/search/object",
                {"search_key": query, "count": 10, "offset": 0,
                 "docs_types": ["doc", "docx", "sheet", "bitable"]}, token=t)
    if resp.get("code") != 0:
        raise RuntimeError(f"Search error: {resp}")
    items = resp.get("data", {}).get("docs_entities", [])
    return [{"title": i.get("title"), "url": i.get("url"), "type": i.get("obj_type")} for i in items]


def move_file(file_token, folder_token, file_type="docx"):
    """移动文件到指定文件夹（file_type: docx/sheet/bitable/file/folder）"""
    t = get_user_token()
    resp = call("POST", f"/open-apis/drive/v1/files/{file_token}/move",
                {"type": file_type, "folder_token": folder_token}, token=t)
    if resp.get("code") != 0:
        raise RuntimeError(f"Move error: {resp}")
    return {"file_token": file_token, "folder_token": folder_token, "status": "moved"}


def list_folders(folder_token=None):
    """递归列出所有文件夹"""
    files = list_files(folder_token)
    folders = [f for f in files if f["type"] == "folder"]

    # 递归获取子文件夹
    all_folders = []
    for folder in folders:
        all_folders.append(folder)
        sub_folders = list_folders(folder["token"])
        all_folders.extend(sub_folders)

    return all_folders


def categorize_document(title, content=""):
    """基于文档标题和内容智能分类"""
    title_lower = title.lower()
    content_lower = content.lower()

    # 产品相关
    if any(word in title_lower for word in ["产品", "需求", "prd", "产品需求", "功能"]):
        return "产品文档"

    # 工作报告
    if any(word in title_lower for word in ["日报", "周报", "月报", "报告", "汇报", "总结"]):
        return "工作报告"

    # 项目管理
    if any(word in title_lower for word in ["项目", "进展", "计划", "规划", "任务"]):
        return "项目管理"

    # 技术文档
    if any(word in title_lower for word in ["技术", "api", "开发", "代码", "架构", "设计"]):
        return "技术文档"

    # 会议记录
    if any(word in title_lower for word in ["会议", "纪要", "记录", "讨论"]):
        return "会议记录"

    # 文件资料
    if any(word in title_lower for word in ["资料", "文件", "库", "收藏", "标签"]):
        return "文件资料"

    # 测试相关
    if any(word in title_lower for word in ["测试", "test", "demo", "样例"]):
        return "测试文档"

    # 默认分类
    return "其他文档"


def get_document_token_from_url(url):
    """从URL提取文档token"""
    if not url:
        return None
    # URL格式类似：https://docs.feishu.cn/docx/TOKEN
    if "/docx/" in url:
        return url.split("/docx/")[-1].split("?")[0]
    elif "/doc/" in url:
        return url.split("/doc/")[-1].split("?")[0]
    return None


def organize_documents():
    """智能整理所有文档"""
    # 使用搜索API获取更全面的文档列表
    print("搜索所有文档...")
    all_docs = []

    # 搜索常见关键词来获取文档
    search_keywords = ["文档", "报告", "产品", "项目", "测试", "技术", "会议", "计划"]
    for keyword in search_keywords:
        try:
            docs = search_docs(keyword)
            all_docs.extend(docs)
        except Exception as e:
            print(f"搜索关键词 '{keyword}' 失败: {e}")

    # 去重
    seen_titles = set()
    unique_docs = []
    for doc in all_docs:
        if doc["title"] not in seen_titles:
            unique_docs.append(doc)
            seen_titles.add(doc["title"])

    print(f"发现 {len(unique_docs)} 个文档")

    # 获取现有文件夹
    existing_folders = {f["name"]: f["token"] for f in list_folders()}

    # 需要创建的文件夹和分类建议
    categories = set()
    classification_plan = {}

    # 分析每个文档
    for doc in unique_docs:
        title = doc["title"]
        if not title:
            continue

        # 基于标题分类
        category = categorize_document(title)
        categories.add(category)

        classification_plan[title] = {
            "category": category,
            "type": doc.get("type", "unknown")
        }

    print(f"识别出 {len(categories)} 个分类: {list(categories)}")

    # 创建缺失的文件夹
    folder_tokens = {}
    created_folders = []
    for category in categories:
        if category in existing_folders:
            folder_tokens[category] = existing_folders[category]
            print(f"文件夹已存在: {category}")
        else:
            print(f"创建文件夹: {category}")
            try:
                result = create_folder(category)
                folder_tokens[category] = result["token"]
                created_folders.append(category)
                print(f"文件夹创建成功: {category} (Token: {result['token']})")
            except Exception as e:
                print(f"创建文件夹失败 {category}: {e}")
                continue

    # 生成分类建议报告
    print("\n=== 文档分类建议 ===")
    for category in sorted(categories):
        docs_in_category = [title for title, info in classification_plan.items() if info["category"] == category]
        if docs_in_category:
            print(f"\n📁 {category} (文件夹Token: {folder_tokens.get(category, 'N/A')}):")
            for doc_title in sorted(docs_in_category):
                print(f"  • {doc_title}")

    print(f"\n=== 整理建议 ===")
    print(f"✅ 已为您创建了 {len(created_folders)} 个新文件夹")
    print(f"📄 分析了 {len(unique_docs)} 个文档")
    print(f"🗂️ 建议分为 {len(categories)} 个分类")
    print(f"\n💡 下一步操作：")
    print(f"1. 打开飞书云空间")
    print(f"2. 按照上述分类建议手动移动文档到对应文件夹")
    print(f"3. 或使用 'move <文档token> <文件夹token>' 命令单个移动")

    return {
        "total_documents": len(unique_docs),
        "categories_created": len(created_folders),
        "categories": list(categories),
        "created_folders": created_folders,
        "folder_tokens": folder_tokens,
        "classification_plan": classification_plan
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "token":
        print(get_token())

    elif cmd == "read":
        if not args:
            print("Usage: read <doc_id>", file=sys.stderr); sys.exit(1)
        print(read_doc(args[0]))

    elif cmd == "create":
        if not args:
            print("Usage: create <title> [folder_token]", file=sys.stderr); sys.exit(1)
        result = create_doc(args[0], args[1] if len(args) > 1 else None)
        print(json.dumps(result, ensure_ascii=False))

    elif cmd == "append":
        if len(args) < 2:
            print("Usage: append <doc_id> <text>", file=sys.stderr); sys.exit(1)
        print(append_text(args[0], args[1]))

    elif cmd == "mkdir":
        if not args:
            print("Usage: mkdir <name> [folder_token]", file=sys.stderr); sys.exit(1)
        result = create_folder(args[0], args[1] if len(args) > 1 else None)
        print(json.dumps(result, ensure_ascii=False))

    elif cmd == "list":
        result = list_files(args[0] if args else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "cal_list":
        result = list_calendars()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "event_create":
        if len(args) < 3:
            print("Usage: event_create <summary> <start> <end> [description]", file=sys.stderr); sys.exit(1)
        result = create_event(args[0], args[1], args[2], args[3] if len(args) > 3 else None)
        print(json.dumps(result, ensure_ascii=False))

    elif cmd == "event_list":
        result = list_events(args[0] if args else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "search":
        if not args:
            print("Usage: search <query>", file=sys.stderr); sys.exit(1)
        result = search_docs(" ".join(args))
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "move":
        if len(args) < 2:
            print("Usage: move <file_token> <folder_token>", file=sys.stderr); sys.exit(1)
        result = move_file(args[0], args[1], args[2] if len(args) > 2 else "docx")
        print(json.dumps(result, ensure_ascii=False))

    elif cmd == "send_image":
        if len(args) < 2:
            print("Usage: send_image <chat_id> <image_path>", file=sys.stderr); sys.exit(1)
        result = send_image_to_chat(args[0], args[1])
        print(json.dumps(result, ensure_ascii=False))

    elif cmd == "send_file":
        if len(args) < 2:
            print("Usage: send_file <chat_id> <file_path>", file=sys.stderr); sys.exit(1)
        result = send_file_to_chat(args[0], args[1])
        print(json.dumps(result, ensure_ascii=False))

    elif cmd == "folders":
        result = list_folders()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "organize":
        result = organize_documents()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
