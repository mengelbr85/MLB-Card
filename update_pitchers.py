#!/usr/bin/env python3
"""
update_pitchers.py — MLB Weather Impact Card · Pitcher Data Updater
====================================================================
Scrapes Baseball Reference for current-season SP stats (all 30 teams),
recalculates HR/9 for every starting pitcher with meaningful innings,
and updates the PITCHERS array + MLB_AVG_HR9 baseline inside
mlb-weather-impact-card.html.

USAGE
-----
  python3 update_pitchers.py                 # update HTML in same folder
  python3 update_pitchers.py --dry-run       # preview changes, don't write
  python3 update_pitchers.py --year 2025     # use a different season
  python3 update_pitchers.py --min-gs 3      # lower GS threshold
  python3 update_pitchers.py --html path/to/card.html

REQUIREMENTS
------------
  Python 3.7+  ·  No external packages (stdlib only)

  First-time Mac users: if you get SSL errors, run this once in Terminal:
    /Applications/Python\ 3.x/Install\ Certificates.command
  (replace 3.x with your Python version number)
"""

import urllib.request
import urllib.error
import re
import time
import os
import sys
import argparse
from datetime import date

# ── Configuration ─────────────────────────────────────────────────────────────

YEAR = date.today().year

# Baseball Reference team codes (our abbr → BR URL code)
BR_CODES = {
    'ARI': 'ARI', 'ATL': 'ATL', 'BAL': 'BAL', 'BOS': 'BOS',
    'CHC': 'CHC', 'CWS': 'CHW', 'CIN': 'CIN', 'CLE': 'CLE', 'COL': 'COL',
    'DET': 'DET', 'HOU': 'HOU', 'KC':  'KCR', 'LAA': 'LAA', 'LAD': 'LAD',
    'MIA': 'MIA', 'MIL': 'MIL', 'MIN': 'MIN', 'NYM': 'NYM', 'NYY': 'NYY',
    'PHI': 'PHI', 'PIT': 'PIT', 'SD':  'SDP', 'SEA': 'SEA', 'SF':  'SFG',
    'STL': 'STL', 'TB':  'TBR', 'TEX': 'TEX', 'TOR': 'TOR', 'WSH': 'WSN',
    # A's: try OAK first, fall back to SAS (Sacramento) if 404
    'ATH': 'OAK',
}

ATH_FALLBACKS = ['OAK', 'SAS', 'LVA']   # try these in order for the A's

TEAM_ORDER = sorted(BR_CODES.keys())

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )
}

BLEND_IP_THRESHOLD = 40
REQUEST_DELAY      = 1.2


# ── Helpers ───────────────────────────────────────────────────────────────────

def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read().decode('utf-8', errors='ignore')
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 10 * (attempt + 1)
                print(f"    Rate limited — waiting {wait}s...")
                time.sleep(wait)
            elif e.code == 404:
                raise   # don't retry 404s
            else:
                if attempt < retries - 1:
                    time.sleep(3)
                else:
                    raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(3)
            else:
                raise
    raise RuntimeError(f"Failed after {retries} attempts")


def parse_ip(ip_str):
    """'118.2' → 118.667  (BR uses .1 = 1/3, .2 = 2/3)"""
    parts = str(ip_str).strip().split('.')
    whole  = int(parts[0])
    thirds = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    return whole + thirds / 3.0


def calc_hr9(hr, ip):
    if ip <= 0:
        return None
    return round(hr * 9.0 / ip, 2)


def blend(actual_hr9, actual_ip, prior_hr9, threshold=BLEND_IP_THRESHOLD):
    if prior_hr9 is None:
        return actual_hr9
    blended = (actual_hr9 * actual_ip + prior_hr9 * threshold) / (actual_ip + threshold)
    return round(blended, 2)


# ── Parsing ───────────────────────────────────────────────────────────────────

# Name pattern: starts with capital letter, contains only letters/spaces/
# hyphens/apostrophes/accented chars — critically NO digits.
# This prevents stat numbers from being captured as part of the name.
_NAME_PAT = r'([A-Z][a-zA-Z\s\.\-\'\u00C0-\u024F]{2,28}?)'

_ROW_PATTERN = re.compile(
    _NAME_PAT    + r'\s+'       # pitcher name (letters only)
    r'(\d{2})\s+'               # age (exactly 2 digits)
    r'SP\s+'                    # position = SP
    r'[\-\d\.]+\s+'             # WAR
    r'\d+\s+\d+\s+'             # W L
    r'[\d\.]+\s+'               # W-L%
    r'[\d\.]+\s+'               # ERA
    r'\d+\s+'                   # G
    r'(\d+)\s+'                 # GS  ← capture
    r'\d+\s+\d+\s+\d+\s+\d+\s+'  # GF CG SHO SV
    r'([\d\.]+)\s+'             # IP  ← capture
    r'\d+\s+\d+\s+\d+\s+'      # H R ER
    r'(\d+)\s'                  # HR  ← capture
)


def parse_team_pitching(html, team_abbr, min_gs, min_ip):
    # Strip HTML tags, decode entities, collapse whitespace
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'&amp;',  '&',  text)
    text = re.sub(r'&nbsp;', ' ',  text)
    text = re.sub(r'&#\d+;', ' ',  text)
    text = re.sub(r'\s+',    ' ',  text)

    pitchers = []
    seen     = set()

    for m in _ROW_PATTERN.finditer(text):
        name   = m.group(1).strip()
        gs     = int(m.group(3))
        ip_str = m.group(4)
        hr     = int(m.group(5))

        # Skip duplicates
        if name in seen:
            continue
        seen.add(name)

        # Skip obviously bad names (too short, or just a single word fragment)
        if len(name) < 4:
            continue

        ip = parse_ip(ip_str)

        if gs < min_gs or ip < min_ip:
            continue

        hr9_val = calc_hr9(hr, ip)
        if hr9_val is None:
            continue

        pitchers.append({
            'name':    name,
            'team':    team_abbr,
            'hr9':     hr9_val,
            'ip':      round(ip, 1),
            'gs':      gs,
            'blended': False,
            'stale':   False,
        })

    return pitchers


def fetch_league_avg_hr9(year):
    """
    Compute MLB avg SP HR/9 from the league standard pitching page.
    Falls back to prior value if parsing fails.
    """
    url = f'https://www.baseball-reference.com/leagues/majors/{year}-standard-pitching.shtml'
    try:
        html = fetch(url)
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text)

        # The league total row contains cumulative HR and IP for all teams.
        # Format: "... {total_IP}.0 {H} {R} {ER} {HR} ..."
        # Total IP is around 25000-26000 for a full season
        m = re.search(r'(\d{5,6})\.0\s+\d+\s+\d+\s+\d+\s+(\d{4,5})\s', text)
        if m:
            total_ip = float(m.group(1))
            total_hr = float(m.group(2))
            avg = round(total_hr * 9 / total_ip, 2)
            if 0.8 < avg < 2.0:   # sanity check
                return avg
    except Exception as e:
        print(f"  Warning: could not fetch league avg ({e}). Using prior value.")
    return None


# ── Prior extraction ──────────────────────────────────────────────────────────

def extract_prior_pitchers(html):
    """Parse the existing PITCHERS array. Returns {name: hr9}."""
    priors = {}
    for m in re.finditer(r"\{name:'([^']+)',\s*team:'([^']+)',\s*hr9:([\d\.]+)\}", html):
        priors[m.group(1)] = {'hr9': float(m.group(3)), 'team': m.group(2)}
    return priors


def extract_mlb_avg(html):
    m = re.search(r'const MLB_AVG_HR9\s*=\s*([\d\.]+)', html)
    return float(m.group(1)) if m else 1.18


# ── JS generation ─────────────────────────────────────────────────────────────

def build_pitchers_js(pitchers, mlb_avg_hr9, updated_date):
    lines = [
        f"// ── Pitcher Database ── Updated {updated_date} ──────────────────────────────────────",
        f"// HR/9 = home runs allowed per 9 IP · MLB avg SP HR/9 ({updated_date[:4]}): {mlb_avg_hr9}",
        f"// * = blended with prior estimate (IP < {BLEND_IP_THRESHOLD})",
        f"const MLB_AVG_HR9 = {mlb_avg_hr9}; // {updated_date[:4]} MLB avg SP HR/9",
        "const PITCHERS = [",
    ]

    last_team = None
    for p in sorted(pitchers, key=lambda x: (x['team'], x['hr9'])):
        if p['team'] != last_team:
            lines.append(f"  // {p['team']}")
            last_team = p['team']
        flag = " // *blended" if p.get('blended') else (
               " // stale"   if p.get('stale')   else "")
        lines.append(f"  {{name:'{p['name']}', team:'{p['team']}', hr9:{p['hr9']}}},{flag}")

    lines.append("];")
    return "\n".join(lines)


# ── HTML patching ─────────────────────────────────────────────────────────────

def patch_html(html, new_pitchers_js):
    # Remove old MLB_AVG_HR9 line (now embedded in new_pitchers_js)
    html = re.sub(r'const MLB_AVG_HR9\s*=\s*[\d\.]+;[^\n]*\n', '', html)

    start = html.find('const PITCHERS = [')
    if start == -1:
        raise ValueError("Could not find 'const PITCHERS = [' in HTML file.")

    depth = 0
    i = start
    end = -1
    while i < len(html):
        if html[i] == '[':
            depth += 1
        elif html[i] == ']':
            depth -= 1
            if depth == 0:
                j = i + 1
                while j < len(html) and html[j] in ' \t':
                    j += 1
                if j < len(html) and html[j] == ';':
                    end = j + 1
                    break
        i += 1

    if end == -1:
        raise ValueError("Could not find end of PITCHERS array.")

    return html[:start] + new_pitchers_js + html[end:]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Update pitcher HR/9 data in mlb-weather-impact-card.html'
    )
    parser.add_argument('--year',     type=int,   default=YEAR)
    parser.add_argument('--min-gs',   type=int,   default=5)
    parser.add_argument('--min-ip',   type=float, default=20.0)
    parser.add_argument('--html',     type=str,   default=None)
    parser.add_argument('--dry-run',  action='store_true')
    parser.add_argument('--no-blend', action='store_true')
    args = parser.parse_args()

    # ── Find HTML file ────────────────────────────────────────────────────────
    if args.html:
        html_path = args.html
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        html_path  = os.path.join(script_dir, 'mlb-weather-impact-card.html')

    if not os.path.exists(html_path):
        print(f"ERROR: HTML file not found: {html_path}")
        print("Place this script in the same folder as mlb-weather-impact-card.html")
        print("or use --html /path/to/card.html")
        sys.exit(1)

    print(f"\n⚾  MLB Weather Impact Card — Pitcher Updater")
    print(f"{'=' * 55}")
    print(f"  Season:   {args.year}")
    print(f"  Min GS:   {args.min_gs}")
    print(f"  Min IP:   {args.min_ip}")
    print(f"  HTML:     {html_path}")
    print(f"  Dry run:  {args.dry_run}")
    print(f"  Blending: {'off' if args.no_blend else f'on (IP < {BLEND_IP_THRESHOLD})'}")
    print()

    with open(html_path, 'r', encoding='utf-8') as f:
        original_html = f.read()

    priors  = extract_prior_pitchers(original_html)
    old_avg = extract_mlb_avg(original_html)
    print(f"  Existing pitchers in HTML: {len(priors)}")
    print(f"  Existing MLB avg HR/9:     {old_avg}")
    print()

    # ── Fetch league average ──────────────────────────────────────────────────
    print("Fetching league average HR/9...")
    mlb_avg = fetch_league_avg_hr9(args.year)
    if mlb_avg is None:
        mlb_avg = old_avg
        print(f"  → Could not compute — keeping {mlb_avg}")
    else:
        print(f"  → {mlb_avg} HR/9")
    print()

    # ── Scrape all 30 teams ───────────────────────────────────────────────────
    all_pitchers = []
    errors       = []

    print(f"Scraping {len(BR_CODES)} team pitching pages from Baseball Reference...")
    print(f"  (1.2s delay between requests)\n")

    for our_abbr in TEAM_ORDER:
        br_code  = BR_CODES[our_abbr]
        fallbacks = ATH_FALLBACKS if our_abbr == 'ATH' else [br_code]
        success  = False

        for code in fallbacks:
            url = f'https://www.baseball-reference.com/teams/{code}/{args.year}-pitching.shtml'
            print(f"  {our_abbr:4} ({code})... ", end='', flush=True)
            try:
                html     = fetch(url)
                pitchers = parse_team_pitching(html, our_abbr, args.min_gs, args.min_ip)
                print(f"{len(pitchers)} starters")
                all_pitchers.extend(pitchers)
                success = True
                break
            except urllib.error.HTTPError as e:
                if e.code == 404 and code != fallbacks[-1]:
                    print(f"404, trying next code... ", end='', flush=True)
                    continue
                else:
                    print(f"FAILED — HTTP {e.code}")
                    errors.append(our_abbr)
                    break
            except Exception as e:
                print(f"FAILED — {e}")
                errors.append(our_abbr)
                break

        if not success and our_abbr not in errors:
            errors.append(our_abbr)

        time.sleep(REQUEST_DELAY)

    print()
    print(f"Scraped {len(all_pitchers)} pitchers across {len(BR_CODES) - len(errors)} teams")
    if errors:
        print(f"  Failed teams: {', '.join(errors)}")
    print()

    # ── Blend small-sample pitchers ───────────────────────────────────────────
    if not args.no_blend:
        blended_count = 0
        for p in all_pitchers:
            if p['ip'] < BLEND_IP_THRESHOLD and p['name'] in priors:
                p['hr9']     = blend(p['hr9'], p['ip'], priors[p['name']]['hr9'])
                p['blended'] = True
                blended_count += 1
        if blended_count:
            print(f"Blended {blended_count} small-sample pitchers with prior estimates")
            print()

    # ── Preserve stale pitchers not found this scrape ─────────────────────────
    found_names = {p['name'] for p in all_pitchers}
    stale = []
    for name, info in priors.items():
        if name not in found_names:
            stale.append({
                'name':    name,
                'team':    info['team'],
                'hr9':     info['hr9'],
                'ip':      0,
                'gs':      0,
                'blended': False,
                'stale':   True,
            })

    if stale:
        print(f"Preserving {len(stale)} pitchers not found in {args.year} scrape")
        print(f"  (IL / retired / < {args.min_gs} GS — kept with prior HR/9)")
        for p in sorted(stale, key=lambda x: x['name'])[:10]:
            print(f"    {p['team']:4} {p['name']:25} {p['hr9']:.2f} [stale]")
        if len(stale) > 10:
            print(f"    ... and {len(stale) - 10} more")
        print()
        all_pitchers.extend(stale)

    # ── Changes summary ───────────────────────────────────────────────────────
    print("=" * 55)
    print("CHANGES SUMMARY")
    print("=" * 55)

    fresh_names = {p['name'] for p in all_pitchers if not p.get('stale')}
    added       = [(p['name'], p['team'], p['hr9'])
                   for p in all_pitchers
                   if not p.get('stale') and p['name'] not in priors]
    updated     = []
    for p in all_pitchers:
        if not p.get('stale') and p['name'] in priors:
            old = priors[p['name']]['hr9']
            if abs(p['hr9'] - old) >= 0.05:
                updated.append((p['name'], p['team'], old, p['hr9']))

    print(f"\n  New pitchers added:    {len(added)}")
    for name, team, hr9 in sorted(added, key=lambda x: x[1]):
        print(f"    + {team:4} {name:25} {hr9:.2f} HR/9")

    print(f"\n  HR/9 updates (≥0.05): {len(updated)}")
    for name, team, old, new in sorted(updated, key=lambda x: abs(x[3]-x[2]), reverse=True)[:20]:
        arrow = "↑" if new > old else "↓"
        print(f"    {arrow} {team:4} {name:25} {old:.2f} → {new:.2f}")
    if len(updated) > 20:
        print(f"    ... and {len(updated) - 20} more")

    print(f"\n  MLB avg HR/9: {old_avg:.2f} → {mlb_avg:.2f}")
    print(f"  Total pitchers: {len(priors)} → {len(all_pitchers)}")
    print()

    if args.dry_run:
        print("DRY RUN — no file written.")
        return

    # ── Write updated HTML ────────────────────────────────────────────────────
    today    = date.today().isoformat()
    new_js   = build_pitchers_js(all_pitchers, mlb_avg, today)
    new_html = patch_html(original_html, new_js)

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(new_html)

    print(f"✅  Updated: {html_path}")
    print(f"    {len(all_pitchers)} pitchers · MLB avg HR/9 = {mlb_avg} · {today}")
    print()


if __name__ == '__main__':
    main()
