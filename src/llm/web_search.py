"""轻量联网搜索：优先必应（国内可访问），DuckDuckGo 兜底——均为免 key 网页版。

只做"搜索结果摘录"级检索，返回标题+摘要（不抓正文）——足够 LLM 组织回答了，
避免把整页塞进上下文烧 token。搜索失败/无结果一律返回空列表，绝不抛异常、
绝不影响主对话流程。
"""
from __future__ import annotations

import logging
import re
from urllib.parse import unquote

import httpx

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_TIMEOUT = 8.0
_MAX_RESULTS = 5


def search(query: str, top: int = _MAX_RESULTS) -> list[dict]:
    """搜索 query，返回 [{title, snippet, url}]。

    依次尝试必应 → DuckDuckGo；全部失败/无结果返回 []，由调用方决定降级。
    """
    if not query or not query.strip():
        return []
    q = query.strip()
    for fetcher in (_search_bing, _search_ddg):
        try:
            results = fetcher(q, top)
            if results:
                return results
        except Exception as e:  # noqa: BLE001
            logger.warning("搜索源 %s 异常: %s", fetcher.__name__, e)
    logger.warning("搜索全部失败: query=%r", q[:60])
    return []


# ---------------- 必应（主源，国内可访问） ----------------

def _search_bing(query: str, top: int) -> list[dict]:
    resp = httpx.get(
        "https://www.bing.com/search",
        params={"q": query},
        headers={"User-Agent": _UA},
        timeout=_TIMEOUT,
        follow_redirects=True,
    )
    if resp.status_code != 200:
        logger.warning("必应请求失败: status=%s", resp.status_code)
        return []
    return _parse_bing(resp.text, top)


def _parse_bing(html: str, top: int) -> list[dict]:
    items = re.findall(r'<li class="b_algo".*?</li>', html, re.DOTALL)
    out: list[dict] = []
    for it in items:
        mh = re.search(
            r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*</h2>',
            it, re.DOTALL)
        if not mh:
            continue
        url = _clean(mh.group(1))
        title = _clean(re.sub(r"<[^>]+>", "", mh.group(2)))
        ms = re.search(r"<p[^>]*>(.*?)</p>", it, re.DOTALL)
        snippet = _clean(re.sub(r"<[^>]+>", "", ms.group(1))) if ms else ""
        if not title:
            continue
        out.append({"title": title, "snippet": snippet, "url": url})
        if len(out) >= top:
            break
    return out


# ---------------- DuckDuckGo（兜底） ----------------

def _search_ddg(query: str, top: int) -> list[dict]:
    resp = httpx.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": _UA},
        timeout=_TIMEOUT,
        follow_redirects=True,
    )
    if resp.status_code != 200:
        return []
    return _parse_ddg(resp.text, top)


def _parse_ddg(html: str, top: int) -> list[dict]:
    titles = re.findall(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
    out: list[dict] = []
    for i, (raw_url, raw_title) in enumerate(titles):
        url = _clean(raw_url)
        m_uddg = re.search(r"uddg=([^&]+)", url)
        if m_uddg:
            try:
                url = unquote(m_uddg.group(1))
            except Exception:  # noqa: BLE001
                pass
        title = _clean(re.sub(r"<[^>]+>", "", raw_title))
        snippet = _clean(re.sub(r"<[^>]+>", "", snippets[i])) if i < len(snippets) else ""
        if not title:
            continue
        out.append({"title": title, "snippet": snippet, "url": url})
        if len(out) >= top:
            break
    return out


# ---------------- 工具 ----------------

def _clean(s: str) -> str:
    s = (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
         .replace("&quot;", '"').replace("&#x27;", "'")
         .replace("&nbsp;", " ").replace("&ensp;", " ").replace("&emsp;", " "))
    return re.sub(r"\s+", " ", s).strip()


def format_results(results: list[dict], limit: int = 4) -> str:
    """把搜索结果格式化成注入用的文本块；空结果返回空串。"""
    if not results:
        return ""
    lines = [
        f"{i + 1}. {r['title']}"
        + (f"：{r['snippet']}" if r.get("snippet") else "")
        for i, r in enumerate(results[:limit])
    ]
    return "\n".join(lines)


# ---------------- 二次元 / 游戏角色考据（直连百科，免费免 key） ----------------

def lookup_acg_lore(name: str, max_chars: int = 2600) -> tuple:
    """为二次元 / 游戏虚拟角色考据：直连萌娘百科 + 中文维基百科抓**正文**，
    免费、免 key、不触发任何厂商「网页搜索插件」单独计费。

    两者都失败才回落到通用搜索（必应 → DDG）的摘要级摘录。
    返回 (考据文本, 来源列表)。

    实测：必应/DDG 对「藿藿」「芙宁娜」等只召回官网/字典，模型据此会幻觉；
    萌娘百科直连命中角色页且关系/设定准确——所以把它放第一位。
    """
    if not name or not name.strip():
        return "", []
    name = name.strip()
    parts: list[str] = []
    sources: list[str] = []
    mg, mg_url = _resolve_moegirl(name)
    if mg:
        parts.append("【萌娘百科】\n" + mg)
        sources.append("萌娘百科: " + mg_url)
    wk = _scrape_wikipedia(name)
    if wk:
        parts.append("【中文维基百科】\n" + wk)
        sources.append("中文维基百科: https://zh.wikipedia.org/wiki/" + name)
    text = "\n\n".join(parts).strip()
    if not text or _looks_like_dict(text):
        # 直连都没拿到像样的角色页 → 再试必应/DDG 摘要（但若仍是字典释义就放弃，让 AI 按名字原创）
        res = search("%s 角色 设定 背景故事" % name, top=3)
        if res and not _looks_like_dict(format_results(res, limit=3)):
            text = format_results(res, limit=3)
            sources = [r["url"] for r in res]
        elif not text:
            # 连直连都为空：返回空，交给调用方（AI 按名字原创 / 用户补充）
            text = ""
            sources = []
    if len(text) > max_chars:
        text = text[:max_chars]
    return text, sources


# 常见 ACG / 游戏作品后缀：同名条目（如「达妮娅」「派蒙」）直接搜纯名字常命中
# 消歧义空页或跨 IP 同名页，带这些后缀重试能命中正确角色页。
_ACG_TITLE_SUFFIXES = (
    "(鸣潮)", "(原神)", "(崩坏：星穹铁道)", "(崩坏星穹铁道)", "(星穹铁道)",
    "(明日方舟)", "(FGO)", "(蔚蓝档案)", "(碧蓝航线)", "(少女前线)", "(崩坏3)",
    "(游戏)", "(动画)", "(小说)",
)


def _resolve_moegirl(name: str) -> tuple:
    """直连萌娘百科并消歧义：纯名字命中短消歧义/跨 IP 同名页时，带作品后缀重试。

    返回 (考据文本, 条目 URL 尾缀)。
    """
    first_text = _scrape_moegirl(name)
    first_url = "https://zh.moegirl.org.cn/" + name
    # 纯名字页就是好角色页 → 直接用
    if first_text and _is_good_hit(first_text, name):
        return first_text, first_url
    # 否则逐个作品后缀重试，取第一个像样的角色页
    for suf in _ACG_TITLE_SUFFIXES:
        t = _scrape_moegirl(name + suf)
        if t and _is_good_hit(t, name):
            return t, "https://zh.moegirl.org.cn/" + name + suf
    # 都没命中更好的：退回纯名字页（哪怕不理想，后面还会走必应兜底）
    if first_text:
        return first_text, first_url
    return "", first_url


def _extract_infobox(html: str) -> str:
    """从萌娘百科条目解析 infobox 的「基础设定」字段（发色 / 瞳色 / 身高 / 体重 /
    年龄 / 生日 / 性别 / 声优 / 别名 / 萌点 / 性格）。

    这些字段在正文 <p> 之外，_extract_lead 抓不到——而角色外观（粉发蓝毛之类）
    恰恰写在 infobox 里。漏抓会导致模型拿不到外貌素材、只能靠记忆幻觉编造。
    """
    _FIELDS = ("发色", "瞳色", "身高", "体重", "年龄", "生日", "性别",
               "声优", "别名", "萌点", "性格", "武器", "出身", "职业", "所属")
    body = re.search(r'<div class="mw-parser-output">(.*?)<div class="printfooter"',
                     html, re.DOTALL)
    seg = body.group(1) if body else html
    # 字段名在 font-weight:bold;text-align:center 的 div 里，值紧跟在下一个 div
    pat = re.compile(
        r'font-weight:\s*bold;\s*text-align:\s*center;">\s*<span>([^<]+)</span>\s*</div>\s*'
        r'<div[^>]*>(.*?)</div>', re.DOTALL)
    found: list[str] = []
    for m in pat.finditer(seg):
        field = m.group(1).strip()
        if field not in _FIELDS:
            continue
        val = re.sub(r"<[^>]+>", "", m.group(2))  # 先去 HTML 标签（含 <a>/<span>）
        val = _clean(val)                          # 再去实体、规范化空白
        if val:
            found.append(f"{field}：{val}")
    return "；".join(found).strip()


def _scrape_moegirl(name: str) -> str:
    """萌娘百科直连：抓 infobox 基础设定 + 正文前若干段落（含角色关系/设定）。"""
    try:
        resp = httpx.get(
            "https://zh.moegirl.org.cn/" + name,
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return ""
        html = resp.text
        ibox = _extract_infobox(html)
        lead = _extract_lead(html)
        parts: list[str] = []
        if ibox:
            parts.append("【基础设定】" + ibox)
        if lead:
            parts.append(lead)
        return "\n".join(parts).strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("萌娘百科抓取失败 %s: %s", name, e)
        return ""


def _scrape_wikipedia(name: str) -> str:
    """中文维基百科 API：取 intro 纯文本（exintro）。"""
    try:
        resp = httpx.get(
            "https://zh.wikipedia.org/w/api.php",
            params={"action": "query", "prop": "extracts",
                    "explaintext": "1", "titles": name,
                    "format": "json", "exintro": "1", "redirects": "1"},
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return ""
        data = resp.json()
        pages = (data.get("query") or {}).get("pages") or {}
        for pid, pg in pages.items():
            if pid == "-1":
                continue
            ext = (pg.get("extract") or "").strip()
            if ext:
                return ext
        return ""
    except Exception as e:  # noqa: BLE001
        logger.warning("维基百科抓取失败 %s: %s", name, e)
        return ""


def _is_wrong_hit(text: str) -> bool:
    """（保留接口）检测是否命中明显的非 ACG 同名页（如所罗门恶魔派蒙）。"""
    return not _is_good_hit(text, "")


def _is_good_hit(text: str, name: str = "") -> bool:
    """判定萌娘百科抓取是否是一段「像样的角色设定」正文，而非字典/神话/消歧义页。

    - 至少 80 字的有效 intro（太短的多是消歧义空段页）；
    - 不含汉字字典释义特征（汉语一级通用规范汉字 / 本义是指 / 拼音：d…）；
    - 不含神话/跨 IP 错配特征（所罗门七十二柱魔神 / 万魔殿 / 登场的恶魔）。
    """
    if not text or len(text) < 80:
        return False
    head = text[:240]
    if any(k in head for k in (
        "所罗门七十二柱魔神", "《万魔殿》", "登场的恶魔", "（恶魔）",
        "七十二柱魔神", "汉语一级通用规范汉字", "本义是指", "始见于商代",
        "为汉语", "拼音：", "拼音:", "部首是", "一级通用规范",
    )):
        return False
    return True


def _looks_like_dict(text: str) -> bool:
    """必应/DDG 回退摘要是否其实是「汉字字典释义页」（如「达」字百度百科）。

    这种素材会让模型把角色名当普通词组望文生义、凭空编世界观，必须丢弃。
    """
    if not text:
        return False
    head = text[:300]
    return any(k in head for k in (
        "汉语一级通用规范汉字", "（汉语文字）", "（汉字）", "本义是指",
        "拼音：", "拼音:", "部首是", "一级通用规范汉字", "始见于商代",
        "汉语字典", "汉典", "汉语国学",
    )) or "百度百科" in text[:200]


def _extract_lead(html: str, max_paras: int = 8, name: str = "") -> str:
    """从 MediaWiki 正文里取前若干有效段落，得到干净、可读的考据素材。

    修正要点（实测翻车点）：
    - 萌娘百科正文里夹着 <style>/<script>，必须剥掉，否则污染段落到不可读；
    - 页面顶部「关系」信息框（封印在她尾巴内的岁阳「尾巴」 / 同事 雪衣、寒鸦）
      会被当成 <p> 抓到最前面——这类是并列名词碎句，没有句号、不含设定，要过滤；
    - 真正的简介段通常以「角色名是……游戏的角色」开头，优先放回最前。
    """
    body = re.search(r'<div class="mw-parser-output">(.*?)<div class="printfooter"',
                     html, re.DOTALL)
    seg = body.group(1) if body else html
    seg = re.sub(r"<style.*?</style>", " ", seg, flags=re.DOTALL)
    seg = re.sub(r"<script.*?</script>", " ", seg, flags=re.DOTALL)
    ps = re.findall(r"<p>(.*?)</p>", seg, re.DOTALL)
    out: list[str] = []
    for p in ps:
        t = _clean(re.sub(r"<[^>]+>", "", p))  # 先去 <a>/<span> 等标签，避免残留链接干扰模型
        if len(t) < 30:
            continue
        if t.startswith("提示"):
            continue
        if "本条目介绍" in t[:20]:
            continue
        # 跳过「关系」信息框碎句：以关系词开头、且无句号（并列名词，不含设定）
        if re.match(r"^(封印|同事|前下属|前任|前代|挚友|战友|眷属|敌人|朋友|"
                    r"上司|下属|搭档|同僚|亲属|师徒|宿敌|相识)", t) and "。" not in t[:40]:
            continue
        out.append(t)
        if len(out) >= max_paras:
            break
    # 优先把「以角色名开头」的简介段放最前（通常是「X 是……游戏的角色」）
    if name:
        out.sort(key=lambda x: 0 if x.startswith(name) else 1)
    return "\n".join(out).strip()
