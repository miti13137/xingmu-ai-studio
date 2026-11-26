"""Local-only story adapter for an explicitly installed Ollama model.

No downloads, cloud endpoints, credentials or paid APIs are used here.
"""
import http.client
import json
import re

HOST = '127.0.0.1'
PORT = 11434
MAX_RESPONSE = 4 * 1024 * 1024
MODEL_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$')
SHOT_SCHEMA = {'type': 'object', 'properties': {'visual': {'type': 'string'}, 'camera': {'type': 'string'}, 'duration': {'type': 'integer'}, 'dialogue': {'type': 'string'}, 'image_prompt': {'type': 'string'}, 'video_prompt': {'type': 'string'}}, 'required': ['visual', 'camera', 'duration', 'dialogue', 'image_prompt', 'video_prompt']}
SCHEMA = {'type': 'object', 'properties': {'title': {'type': 'string'}, 'script': {'type': 'string'}, 'characters': {'type': 'array', 'items': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'description': {'type': 'string'}}, 'required': ['name', 'description']}}, 'shots': {'type': 'array', 'items': SHOT_SCHEMA}}, 'required': ['title', 'script', 'characters', 'shots']}


def request(path, payload=None, timeout=5):
    conn = http.client.HTTPConnection(HOST, PORT, timeout=timeout)
    try:
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        conn.request('POST' if body is not None else 'GET', path, body=body, headers={'Content-Type': 'application/json'} if body else {})
        response = conn.getresponse()
        data = response.read(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE:
            raise ValueError('本機模型回傳資料過大')
        if response.status != 200:
            raise ValueError(f'本機模型服務錯誤（HTTP {response.status}）')
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError('本機模型回傳格式錯誤')
        return value
    except (OSError, TimeoutError) as exc:
        raise ValueError('無法連線本機模型服務；請確認 Ollama 已安裝並啟動') from exc
    except json.JSONDecodeError as exc:
        raise ValueError('本機模型回傳的資料不是有效 JSON') from exc
    finally:
        conn.close()


def models():
    result = []
    available = request('/api/tags').get('models', [])
    if not isinstance(available, list):
        raise ValueError('本機模型清單格式錯誤')
    for item in available:
        if not isinstance(item, dict):
            continue
        name = item.get('name', '')
        if not isinstance(name, str) or not MODEL_RE.fullmatch(name) or 'cloud' in name.lower() or not isinstance(item.get('size'), int) or item['size'] <= 0:
            continue
        result.append(name)
    return result


def generate(model, title, idea, ratio, kind):
    for value, label, limit in [(model, '模型名稱', 160), (title, '作品名稱', 120), (idea, '故事構想', 5000)]:
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(label + '不可空白或過長')
    if ratio not in ('9:16', '16:9') or kind not in ('短劇', 'MV'):
        raise ValueError('類型或比例錯誤')
    if model not in models():
        raise ValueError('請選擇本機已安裝的非雲端模型')
    details = request('/api/show', {'model': model})
    if details.get('remote_model') or details.get('remote_host') or 'cloud' in model.lower():
        raise ValueError('此模型屬於遠端模型，已禁止呼叫')
    prompt = ('請根據下列資料創作完整繁體中文故事與可拍攝分鏡。以 JSON 回答。'
              '保留角色設定、外觀和服裝一致性，寫自然對白；分鏡含畫面、運鏡、秒數、對白、'
              '圖片與影片提示詞。每鏡 1 至 120 秒，最多 50 鏡。這些提示詞不代表媒體已生成。\n'
              + json.dumps({'作品名稱': title, '故事構想': idea, '類型': kind, '畫面比例': ratio}, ensure_ascii=False))
    result = request('/api/chat', {'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'format': SCHEMA, 'stream': False}, timeout=600)
    if not result.get('done') or result.get('done_reason') == 'length':
        raise ValueError('本機模型未完整生成，請縮短故事構想或調整模型設定')
    try:
        project = json.loads(result['message']['content'])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('AI 回覆不是有效的作品 JSON') from exc
    if not isinstance(project, dict) or not isinstance(project.get('script'), str) or not project['script'].strip():
        raise ValueError('AI 未產生有效劇本')
    if not isinstance(project.get('characters'), list) or not 1 <= len(project['characters']) <= 30:
        raise ValueError('AI 角色資料不完整')
    if not isinstance(project.get('shots'), list) or not 1 <= len(project['shots']) <= 50:
        raise ValueError('AI 分鏡資料不完整')
    for c in project['characters']:
        if not isinstance(c, dict) or any(not isinstance(c.get(k), str) or not c[k].strip() for k in ('name', 'description')):
            raise ValueError('AI 角色設定格式不完整')
    for shot in project['shots']:
        if not isinstance(shot, dict) or any(not isinstance(shot.get(k), str) for k in ('visual', 'camera', 'dialogue', 'image_prompt', 'video_prompt')) or not shot['visual'].strip() or not isinstance(shot.get('duration'), int) or isinstance(shot['duration'], bool) or not 1 <= shot['duration'] <= 120:
            raise ValueError('AI 分鏡格式不完整')
    return {'name': title, 'type': kind, 'ratio': ratio, 'style': '電影寫實', 'idea': idea, 'script': project['script'], 'characters': project['characters'], 'shots': project['shots'], 'audio': '', 'notes': '本機 AI 生成草稿，需人工檢查；圖片、影片及配音尚未生成。'}
