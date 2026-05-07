import re
import yaml
from utils import norm_text as _norm_base

def split_front_matter(md: str):
    if not md.startswith("---"):
        return {}, md
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", md, flags=re.DOTALL)
    if not m:
        return {}, md
    yaml_txt, body = m.group(1), m.group(2)
    try:
        meta = yaml.safe_load(yaml_txt) or {}
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}
    return meta, body

def strip_first_h1(md: str) -> str:
    return re.sub(r'^#\s+.*\n', '', md, count=1)

def demote_markdown_headings(md: str, shift: int = 2) -> str:
    def repl(m):
        hashes, title = m.group(1), m.group(2)
        lvl = min(len(hashes) + shift, 6)
        return "#" * lvl + " " + title
    return re.sub(r'^(#{1,6})\s+(.+)$', repl, md, flags=re.MULTILINE)

def parse_markdown_headings(md: str):
    headings = []
    for m in re.finditer(r'^(#{1,6})\s+(.+)$', md, flags=re.MULTILINE):
        headings.append((len(m.group(1)), m.group(2).strip(), m.start()))
    return headings

def extract_section_by_heading(md: str, heading_title: str, levels=(2,3)):
    headings = parse_markdown_headings(md)

    def norm(s: str) -> str:
        # Normaliza tildes + espacios para comparación robusta
        return re.sub(r"\s+", " ", _norm_base(s).strip())

    target = norm(heading_title)

    target_idx = None
    # exact
    for i, (lvl, title, start) in enumerate(headings):
        if lvl in levels and norm(title) == target:
            target_idx = i
            break
    # contains
    if target_idx is None:
        for i, (lvl, title, start) in enumerate(headings):
            if lvl in levels and target in norm(title):
                target_idx = i
                break
    if target_idx is None:
        return ""

    start_pos = headings[target_idx][2]
    end_pos = len(md)
    current_lvl = headings[target_idx][0]
    for j in range(target_idx + 1, len(headings)):
        lvl, _, stpos = headings[j]
        if lvl <= current_lvl:
            end_pos = stpos
            break
    return md[start_pos:end_pos].strip()

def key_topics_from_module(md_full: str, level: int = 2, max_topics: int = 8):
    meta, body = split_front_matter(md_full)

    kt = meta.get("key_topics")
    if isinstance(kt, list) and kt:
        out = []
        for t in kt:
            if isinstance(t, dict) and t.get("enabled", True):
                out.append({"label": t.get("label",""), "heading": t.get("heading", t.get("label",""))})
        return out[:max_topics]

    headings = parse_markdown_headings(body)
    topics = [title for (lvl, title, _) in headings if lvl == level]
    seen, out = set(), []
    for t in topics:
        tl = t.lower()
        if tl in seen:
            continue
        seen.add(tl)
        out.append({"label": t, "heading": t})
    return out[:max_topics]

def module_label_from_md(stem: str, md_text: str) -> str:
    meta, _ = split_front_matter(md_text)

    nav_label = meta.get("nav_label")
    if isinstance(nav_label, str) and nav_label.strip():
        return nav_label.strip()

    mod = meta.get("module")
    title = meta.get("title") or meta.get("module_title")
    if title:
        if mod is not None:
            return f"{str(mod).zfill(2)} · {str(title).strip()}" if str(mod).isdigit() else f"{mod} · {str(title).strip()}"
        return str(title).strip()

    return stem.replace("_"," ").strip()