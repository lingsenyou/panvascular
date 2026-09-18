# -*- coding: utf-8 -*-
"""Bake the Panvascular Radar snapshot.

Search comes from PubMed E-utilities: free, keyless, CORS-open, no daily budget.
Citation counts come from OpenAlex single-record lookups by DOI, which cost zero
credits even when the OpenAlex daily budget is exhausted.

Run it by hand, then commit snapshot.json and lenses.json.
"""
import datetime
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request

UA = "panvascular-radar/1.0 (mailto:lingsenyou@fudan.edu.cn)"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
OA_WORK = "https://api.openalex.org/works/doi:"
RECENT_DAYS = 60
TA = "[Title/Abstract]"

LENSES = [
    ("suitcordance", "Suitcordance", "suitcordance" + TA),
    ("ivi", "Intravascular imaging",
     '"intravascular ultrasound"{0} OR "intravascular imaging"{0} OR "intracoronary imaging"{0} OR "intravascular optical coherence tomography"{0}'.format(TA)),
    ("brs", "Bioresorbable scaffolds",
     '"bioresorbable vascular scaffold"{0} OR "bioresorbable coronary scaffold"{0} OR "bioresorbable stent"{0} OR "biodegradable vascular stent"{0}'.format(TA)),
    ("dcb", "Drug-coated balloons",
     '"drug-coated balloon"{0} OR "drug-eluting balloon"{0} OR "paclitaxel-coated balloon"{0} OR "sirolimus-coated balloon"{0}'.format(TA)),
    ("flex", "Flexible electronics",
     '("flexible electronics"{0} OR "stretchable electronics"{0} OR "epicardial electronics"{0} OR "wearable cardiac sensor"{0}) AND (cardiac{0} OR cardiovascular{0} OR vascular{0})'.format(TA)),
    ("dt", "Digital twins",
     '"cardiac digital twin"{0} OR "cardiovascular digital twin"{0} OR "coronary digital twin"{0} OR "hemodynamic digital twin"{0}'.format(TA)),
    ("ai", "AI in coronary imaging",
     '("deep learning"{0} OR "convolutional neural network"{0}) AND ("coronary angiography"{0} OR "coronary computed tomography"{0} OR "percutaneous coronary intervention"{0} OR "coronary artery calcium"{0})'.format(TA)),
    ("chip", "Vessel-on-a-chip",
     '"vessel-on-a-chip"{0} OR "vasculature-on-a-chip"{0} OR "vascular organ-on-a-chip"{0} OR "vascularized organ-on-a-chip"{0}'.format(TA)),
]


def fetch(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as exc:
            if i == tries - 1:
                print("  ! failed %s (%s)" % (url[:90], exc), file=sys.stderr)
                return None
            time.sleep(2 + 2 * i)


def esearch(term, retmax=0, mindate=None):
    q = {"db": "pubmed", "retmode": "json", "retmax": str(retmax), "sort": "date", "term": term}
    if mindate:
        q.update({"datetype": "pdat", "mindate": mindate, "maxdate": "3000/12/31"})
    raw = fetch(EUTILS + "/esearch.fcgi?" + urllib.parse.urlencode(q))
    if not raw:
        return {"count": 0, "ids": []}
    d = json.loads(raw).get("esearchresult", {})
    return {"count": int(d.get("count", 0) or 0), "ids": d.get("idlist", [])}


def esummary(ids):
    if not ids:
        return []
    raw = fetch(EUTILS + "/esummary.fcgi?" + urllib.parse.urlencode(
        {"db": "pubmed", "retmode": "json", "id": ",".join(ids)}))
    if not raw:
        return []
    r = json.loads(raw).get("result", {})
    return [r[u] for u in r.get("uids", []) if u in r]


def efetch_abstracts(ids):
    if not ids:
        return {}
    raw = fetch(EUTILS + "/efetch.fcgi?" + urllib.parse.urlencode(
        {"db": "pubmed", "retmode": "xml", "rettype": "abstract", "id": ",".join(ids)}))
    if not raw:
        return {}
    out = {}
    for chunk in raw.split("<PubmedArticle>")[1:]:
        m = re.search(r"<PMID[^>]*>(\d+)</PMID>", chunk)
        if not m:
            continue
        parts = re.findall(r"<AbstractText[^>]*>(.*?)</AbstractText>", chunk, re.S)
        text = re.sub(r"\s+", " ", " ".join(re.sub(r"<[^>]+>", "", p) for p in parts)).strip()
        if text:
            out[m.group(1)] = text[:1500]
    return out


def openalex_by_doi(doi):
    if not doi:
        return {}
    url = (OA_WORK + urllib.parse.quote(doi, safe="/.:-") +
           "?select=id,cited_by_count,open_access,best_oa_location,fwci,"
           "citation_normalized_percentile,type,is_retracted&mailto=lingsenyou@fudan.edu.cn")
    raw = fetch(url, tries=2)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def card(item, lens_key, abstracts):
    ids = {x.get("idtype"): x.get("value") for x in item.get("articleids", [])}
    doi = (ids.get("doi") or "").strip()
    pmid = str(item.get("uid") or "")
    authors = [a.get("name", "") for a in item.get("authors", []) if a.get("name")]
    pubtypes = [t for t in (item.get("pubtype") or []) if t and t != "Journal Article"]
    oa = openalex_by_doi(doi) if doi else {}
    cnp = oa.get("citation_normalized_percentile") or {}
    boa = oa.get("best_oa_location") or {}
    return {
        "id": (oa.get("id") or "").replace("https://openalex.org/", ""),
        "doi": doi,
        "pmid": pmid,
        "t": (item.get("title") or "").rstrip("."),
        "v": item.get("source") or item.get("fulljournalname") or "",
        "d": (item.get("sortpubdate") or "")[:10].replace("/", "-"),
        "au": authors[:3] + ([str(len(authors))] if len(authors) > 3 else []),
        "ab": abstracts.get(pmid, ""),
        "c": oa.get("cited_by_count"),
        "oa": bool((oa.get("open_access") or {}).get("is_oa")),
        "pdf": boa.get("pdf_url") or "",
        "fwci": oa.get("fwci"),
        "top10": bool(cnp.get("is_in_top_10_percent")),
        "r": bool(oa.get("is_retracted")),
        "ty": oa.get("type") or "article",
        "pt": pubtypes[:3],
        "lens": lens_key,
    }


def main():
    today = datetime.date.today()
    since = today - datetime.timedelta(days=RECENT_DAYS)
    since_pm = since.strftime("%Y/%m/%d")
    out = {"generated": today.isoformat(), "since": since.isoformat(), "days": RECENT_DAYS,
           "lenses": {}, "feed": [], "tracker": {}}

    for key, label, term in LENSES:
        total = esearch(term)["count"]
        time.sleep(0.4)
        rec = esearch(term, retmax=8, mindate=since_pm)
        time.sleep(0.4)
        items = esummary(rec["ids"])
        time.sleep(0.4)
        abstracts = efetch_abstracts([str(i.get("uid")) for i in items])
        time.sleep(0.4)
        cards = [card(it, key, abstracts) for it in items]
        out["lenses"][key] = {"label": label, "total": total, "recent": rec["count"], "items": cards}
        print("%-13s total=%-8d recent=%-5d kept=%d" % (key, total, rec["count"], len(cards)), file=sys.stderr)

    seen, feed = set(), []
    for key, _label, _term in LENSES:
        for it in out["lenses"][key]["items"]:
            k = it["doi"] or it["pmid"]
            if k in seen:
                continue
            seen.add(k)
            feed.append(it)
    feed.sort(key=lambda x: x.get("d") or "", reverse=True)
    out["feed"] = feed[:20]

    tr = esearch("suitcordance", retmax=50)
    time.sleep(0.4)
    tr_items = esummary(tr["ids"])
    time.sleep(0.4)
    cites, years, oa_n = 0, [], 0
    for it in tr_items:
        ids = {x.get("idtype"): x.get("value") for x in it.get("articleids", [])}
        w = openalex_by_doi((ids.get("doi") or "").strip())
        cites += w.get("cited_by_count") or 0
        if (w.get("open_access") or {}).get("is_oa"):
            oa_n += 1
        y = (it.get("sortpubdate") or "")[:4]
        if y.isdigit():
            years.append(int(y))
        time.sleep(0.2)
    out["tracker"] = {"count": tr["count"], "cites": cites, "first": min(years) if years else None,
                      "oa": oa_n, "of": len(tr_items)}

    io.open("snapshot.json", "w", encoding="utf-8").write(
        json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    io.open("lenses.json", "w", encoding="utf-8").write(json.dumps(
        {"generated": out["generated"], "since": out["since"], "lenses": out["lenses"]},
        ensure_ascii=False, separators=(",", ":")))
    print("feed=%d tracker=%s" % (len(out["feed"]), out["tracker"]), file=sys.stderr)


main()
