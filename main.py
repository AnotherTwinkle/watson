import pywikibot
import enchant
import re
import sys 
import random
import time
import shlex
from datetime import datetime

from rapidfuzz import fuzz
from functools import lru_cache
from urllib.parse import urlparse, parse_qs, unquote

import clicore
import json
import mwparserfromhell
import pyperclip
import webbrowser
import tempfile
import html
import urllib.parse

DEBUGGER_ADDRESS = "127.0.0.1:9222"

parser = clicore.Parser()

class bcolors:
	HEADER = '\033[95m'
	OKBLUE = '\033[94m'
	OKCYAN = '\033[96m'
	OKGREEN = '\033[92m'
	WARNING = '\033[93m'
	FAIL = '\033[91m'
	ENDC = '\033[0m'
	BOLD = '\033[1m'
	UNDERLINE = '\033[4m'

spell_dict = enchant.Dict("en_US")
site = pywikibot.Site("en", "wikipedia")

state = json.load(open("state.json", 'r'))
config = json.load(open("config.json", 'r'))
saved = json.load(open("saved.json", 'r'))
wpbd = json.load(open('wpbd.json', 'r'))
func = {
	"clipboard" : lambda : pyperclip.paste(), # Clipboard
	"randpage" : lambda :  next(site.randompages(total=1, namespaces=[0])),
	"randbdpage" : lambda : pywikibot.Page(site, random.choice(list(wpbd.keys())))
}

VARIABLES = {"state": state, "config" : config, "saved" : saved, "func" : func}

@lru_cache(maxsize=50000)
def iswordok(word):
	return spell_dict.check(word)

@lru_cache(maxsize=50000)
def fuzzy_similarity(word):
	suggestions = spell_dict.suggest(word)
	if not suggestions:
		return (0, None)

	# Compare with best suggestion
	best = suggestions[0]

	# Ratio is 0–100
	score = fuzz.ratio(word.lower(), best.lower())
	return (score, best)

def get_wikipedia_title(url: str) -> str:
	"""
	Extract and decode only the Wikipedia page title from a URL.
	Supports:
	  - /wiki/Title_format
	  - /w/index.php?title=Title_format&...
	Preserves underscores.
	"""
	parsed = urlparse(url)
	path = parsed.path

	# Case 1: /wiki/Title_format
	if path.startswith("/wiki/"):
		title = path[len("/wiki/"):]

	# Case 2: w/index.php?title=Title_format
	elif path.endswith("index.php"):
		query = parse_qs(parsed.query)
		title = query.get("title", [""])[0]

	# Fallback: take last segment
	else:
		title = path.split("/")[-1]

	return unquote(title)

def spellcheck(arg, ignore=[]):
	ignore = [t.lower() for t in ignore]

	typos = set()
	if isinstance(arg, str):
		text = arg
	elif isinstance(arg, pywikibot.Page):
		code = mwparserfromhell.parse(arg.text)
		text = code.strip_code()
	else:
		return ""

	tokens = re.findall(r"[A-Za-z']+|[^A-Za-z']+", text)
	result = []
	freqmap = dict()

	for token in tokens:
		if not token.isalpha():
			continue

		lower = token.lower()
		if freqmap.get(lower, None) is None:
			freqmap[lower] = 1
		else:
			freqmap[lower] += 1

	for token in tokens:
		if not token.isalpha():
			result.append(token)
			continue

		lower = token.lower()

		if lower in ignore:
			result.append(token)
			continue

		if not iswordok(lower) \
		   and lower not in ignore \
		   and freqmap[lower] < 3:
			score, best = fuzzy_similarity(lower)

			if score == 100:
				pass
			elif score >= 80:
				# obvious typo → RED
				typos.add((lower, best, score))
				token = f"{bcolors.FAIL}{token}{bcolors.ENDC}"
			else:
				# likely foreign word → YELLOW
				token = f"{bcolors.WARNING}{token}{bcolors.ENDC}"

		result.append(token)

	return "".join(result), typos

class MaybeVariableConverter(clicore.Converter):
	PREFIX_RE = re.compile(r"@(\w+):(.+)")  # Matches @prefix:value

	@classmethod
	def isvalid(cls, argument):
		return bool(cls.PREFIX_RE.match(argument))

	@classmethod
	def convert(cls, argument):
		match = cls.PREFIX_RE.match(argument)
		if not match:
			return argument

		prefix, key = match.groups()
		try:
			# Fetch from a dynamic namespace (like state, other, etc.)
			if prefix == "func": # func is a special prefix for functions
				value = VARIABLES[prefix][key]()
			else:
				value = VARIABLES[prefix][key]
		except KeyError:
			raise ValueError(f"No value found for {prefix}:{key}")
		except Exception as e:
			raise e

		return value

def get_page_from_url(url):
	title = get_wikipedia_title(url)
	page = pywikibot.Page(site, title)
	return page

@parser.command(name = "clipboardsend", aliases = ['cps'])
def cmd_clipboard_send(ctx, entry : MaybeVariableConverter):
	pyperclip.copy(entry)

@parser.command(name = "setpage")
def cmd_setpageurl(ctx, page : MaybeVariableConverter):
	state['page'] = page
	print(f"Page set : {page}")

@parser.add_flag(name = 'nsc', default = False)
@parser.add_flag(name = 'nsm', default = False)
@parser.add_flag(name = 'cpurl', default = False)
@parser.command(name = "spellcheck", aliases = ['sp'])
def cmd_spellcheck(ctx, page : MaybeVariableConverter):
	if not isinstance(page, pywikibot.Page):
		# Likely an url
		page = get_page_from_url(page)

	if not page.exists():
		print(f"Cannot find page - {title}")
		return

	print("\n")
	out, typos = spellcheck(page, ignore = [])

	if not ctx.flags.nsc:
		print(out)
		print()

	if not ctx.flags.nsm:
		typos = list(typos)

		typos.sort(key = lambda m : m[2], reverse = True)
		print("Possible typos\n")
		print('\n'.join([f'{i[0]} -> {i[1]}' for i in typos]))
		print()

	if ctx.flags.cpurl:
		pyperclip.copy(page.full_url())

	print(f'URL : {page.full_url()}')


@parser.add_flag(name = "save", default = None)
@parser.command(name = "backlinks", aliases = ['bl'])
def cmd_get_backlinks(ctx, page:  MaybeVariableConverter = "@state:page"):
	page = MaybeVariableConverter.convert(page)
	page = get_page_from_url(page)

	links = list(page.backlinks())
	s = ""
	for page in links:
		s += f"{page.title()} \n {page.full_url()}\n\n"

	print(s)

	if ctx.flags.save:
		with open(ctx.flags.save, 'w') as f:
			f.write(s)

# Commands specific to the task of updating talk pages with Wikiproject Bangladesh template
def has_wpbd(page : pywikibot.Page):
	# Important, provide with the original page, not talk
	talk = page.toggleTalkPage()
	if not talk.exists():
		return False

	text = talk.text
	return bool(re.search(r'\{\{\s*WikiProject\s+Bangladesh', text, re.IGNORECASE))

@parser.command(name = "dl_wpbd")
def cmd_dl_wpbd(ctx, category):
	# Download all WikiProject Bangladesh articles and save them
	cat = pywikibot.Category(site, "Category:WikiProject Bangladesh articles")

	data = {}
	for page in cat.articles():
		page = page.toggleTalkPage()
		data[page.title()] = page.full_url()
		print(page.title())

	with open('wpbd.json', 'w') as f:
		json.dump(data, f, indent = 4)

@parser.add_flag(name="history", default=False)
@parser.add_flag(name="geography", default=False)
@parser.add_flag(name="attention", default=False)
@parser.add_flag(name="class_val", type=str, default=None)
@parser.add_flag(name="line", type=int, default=0)
@parser.command(name="prepend_wpbd", aliases=['pwpbd'])
def cmd_prepend_wpbd(ctx):
	if state['checkout']:
		print("There is a change yet to be pushed. Please resolve it first.")
		return

	page = get_page_from_url(state['page'])
	talk = page.toggleTalkPage()
	if has_wpbd(page):
		print("wpbd template already exists in this page.")
		return

	args = {}
	if ctx.flags.class_val:
		args["class"] = ctx.flags.class_val
	if ctx.flags.attention:
		args["attention"] = "yes"
	if ctx.flags.history:
		args["history"] = "yes"
	if ctx.flags.geography:
		args["geography"] = "yes"

	# Build template text
	template_text = "{{WikiProject Bangladesh"
	for key, val in args.items():
		template_text += f'|{key}={val}'
	template_text += "}}\n"

	# Load talk page text and split into lines
	talk_text = talk.text if talk.exists() else ""
	lines = talk_text.splitlines()

	insert_line = ctx.flags.line
	# Clamp insert_line to valid range
	if insert_line < 0:
		insert_line = 0
	elif insert_line > len(lines):
		insert_line = len(lines)

	# Insert the template at the specified line
	lines.insert(insert_line, template_text.rstrip('\n'))

	# Join back the text
	new_talk_text = "\n".join(lines)

	# Update state
	state['checkout'] = (page.full_url(), new_talk_text)
	print(f"WikiProject Bangladesh template added at line {insert_line} of the talk page.")


# Commands Specific to the task of adding the dmy/mdy tags
def extract_dates(wikitext: str):
	"""
	Extract dates in the formats:
	  1) NUM MONTHNAME [,] YEAR  -> DMY
	  2) MONTHNAME NUM [,] YEAR  -> MDY

	Ignores any dates inside <ref>...</ref> tags.

	Returns list of dicts with:
		- text
		- is_dmy
		- text_toggled
		- datetime
	"""

	# Remove all <ref>...</ref> contents to ignore them
	text = re.sub(r"<ref[^>]*>.*?</ref>", "", wikitext, flags=re.DOTALL|re.IGNORECASE)

	month_names = [
		"January", "February", "March", "April", "May", "June",
		"July", "August", "September", "October", "November", "December"
	]
	months_regex = "|".join(month_names)
	month_to_number = {m: i + 1 for i, m in enumerate(month_names)}

	pattern = re.compile(
		rf"""\b(
			(?P<d1>\d{{1,2}})\s+(?P<m1>{months_regex})\s*(?P<c1>,?)\s*(?P<y1>\d{{4}})
			|
			(?P<m2>{months_regex})\s+(?P<d2>\d{{1,2}})\s*(?P<c2>,?)\s*(?P<y2>\d{{4}})
		)\b""",
		re.VERBOSE
	)

	results = []

	for match in pattern.finditer(text):
		if match.group("d1"):  # DMY
			day = int(match.group("d1"))
			month = match.group("m1")
			year = int(match.group("y1"))
			comma = match.group("c1")
			is_dmy = True
		else:  # MDY
			day = int(match.group("d2"))
			month = match.group("m2")
			year = int(match.group("y2"))
			comma = match.group("c2")
			is_dmy = False

		month_num = month_to_number[month]
		dt = datetime(year, month_num, day).date()

		# Preserve comma presence when toggling
		comma_part = "," if comma else ""
		if is_dmy:
			toggled = f"{month} {day}{comma_part} {year}"
		else:
			toggled = f"{day} {month}{comma_part} {year}"

		results.append({
			"text": match.group(0),
			"is_dmy": is_dmy,
			"text_toggled": toggled,
			"datetime": dt
		})

	return results

@parser.command(name="extractdates", aliases = ["ed"])
def cmd_extract_dates(ctx, page : MaybeVariableConverter):
	page = get_page_from_url(page)
	for j in extract_dates(page.text):
		print(j)

class ND_ERRORS:
	CANNOT_FIND_PAGE = 1
	NON_MAINSPACE = 2
	TEMPLATE_PRESENT = 3
	NO_DATE_PRESENT = 4


@parser.add_flag(name = "onlymainspace", default = False)
@parser.add_flag(name = 'checkout', default = False)
@parser.add_flag(name="force", default = None)
@parser.add_flag(name="nodatechange", default = False)
@parser.add_flag(name="line", type=int, default=0)
@parser.add_flag(name="takelineprompt", default = False)
@parser.command(name="normalize_dates", aliases=["nd"])
def cmd_normalize_dates(ctx, url: MaybeVariableConverter):
	if state['checkout']:
		print("There is a change yet to be pushed. Please resolve it first.")
		return

	if isinstance(url, pywikibot.Page):
		page = url
	else:
		page = get_page_from_url(url)

	if not page.exists():
		print(f"Cannot find page - {page.title()}")
		return ND_ERRORS.CANNOT_FIND_PAGE

	print(f"Title : {page.title()}")
	print(f"URL : {page.full_url()}")
	text = page.text

	if ctx.flags.onlymainspace and ":" in page.title().split()[0]:
		print(f"{bcolors.FAIL}Skipping non mainspace article.{bcolors.ENDC}")
		return ND_ERRORS.NON_MAINSPACE

	# ---- Detect ehttps://en.wikipedia.org/wiki/Wikipedia:Manual_of_Style/Dates_and_numbersxisting date templates (robust variation handling) ----
	date_template_pattern = re.compile(
		r"""\{\{\s*(
				(?:Use\s*)?(?:dmy|mdy)(?:\s*dates?)?
			)\b[^}]*\}\}""",
		re.IGNORECASE | re.VERBOSE
	)

	match = date_template_pattern.search(text)
	if match:
		print(f"{bcolors.FAIL}Date template already present: {match.group(1)}{bcolors.ENDC}")
		return ND_ERRORS.TEMPLATE_PRESENT

	# ---- Extract dates ----
	dates = extract_dates(text)

	dmy_count = sum(1 for d in dates if d["is_dmy"])
	mdy_count = len(dates) - dmy_count

	majority_is_dmy = None
	changes = 0
	new_text = text

	if ctx.flags.force == "dmy":
		majority_is_dmy = True
	elif ctx.flags.force == "mdy":
		majority_is_dmy = False
	elif ctx.flags.force:
		print(f"{bcolors.FAIL}Unknown option for flag: force{bcolors.ENDC}")
		return

	if dates:
		if not ctx.flags.force:
			if dmy_count == 0:
				majority_is_dmy = False
			elif mdy_count == 0:
				majority_is_dmy = True
			else:
				majority_is_dmy = dmy_count > mdy_count

		majority_label = "DMY" if majority_is_dmy else "MDY"
		print(f"DMY: {dmy_count} | MDY: {mdy_count}")
		print(f"Using Template: {majority_label}" + (" (forced)" if ctx.flags.force else ""))

		if not ctx.flags.nodatechange:
			# Normalize minority dates
			for d in dates:
				if d["is_dmy"] != majority_is_dmy:
					new_text = new_text.replace(d["text"], d["text_toggled"], 1)
					changes += 1

			print(f"Toggled {changes} date(s) to {majority_label} format.")
			if changes > 5:
				print(f"{bcolors.FAIL}Warning : {changes} changes made, please manually review before checking out.{bcolors.ENDC}")
		else:
			print("Making no changes to dates as requested.")
	else:
		if not ctx.flags.force:
			print(f"{bcolors.FAIL}Article has no detectable dates. Terminating.{bcolors.ENDC}")
			return ND_ERRORS.NO_DATE_PRESENT
		else:
			print(f"{bcolors.WARNING}Article has no detectable dates, but continuing because force flag is set.{bcolors.ENDC}")

	# ---- Insert correct template with current month/year ----
	now = datetime.now()
	month_year = now.strftime("%B %Y")

	if majority_is_dmy:
		template = f"{{{{Use dmy dates|date={month_year}}}}}\n"
	else:
		template = f"{{{{Use mdy dates|date={month_year}}}}}\n"

	lines = new_text.splitlines()
	insert_line = ctx.flags.line
	summary = f"add " + template

	if ctx.flags.takelineprompt:
		print("First 5 lines of wikitext:\n")
		for i in range(5):
			print(f'{i} {lines[i]}')
		print()
		insert_line = input("Line to insert > ")

	if insert_line in ["abort", "nogo", "x"]:
		print(f"{bcolors.FAIL}Aborting.{bcolors.ENDC}")
		return

	insert_line = int(insert_line)
	if insert_line < 0:
		insert_line = 0
	elif insert_line > len(lines):
		insert_line = len(lines)

	old_lines = lines.copy()
	lines.insert(insert_line, template.rstrip("\n"))
	final_text = "\n".join(lines)

	state['checkout'] = (page.full_url(), final_text, summary)

	print(f"Template added at line {insert_line}.")
	print(f"Checkout prepared for: {page.title()} -> {summary}")

	if ctx.flags.checkout:
		parser.get_command('checkout').invoke(ctx)

	return {
		"title" : page.title(),
		"url" : page.full_url(),
		"dmy" : dmy_count,
		"mdy" : mdy_count,
		"first_lines" : old_lines[:5]
	}

@parser.add_flag(name = 'delay', type = float, default = 0)
@parser.command(name="bulk_page_template_check", aliases = ["bptc"])
def cmd_bulk_page_template_check(ctx):
	parser.parse("cco", [])
	hold = True
	for title, url in sorted(list(wpbd.items())):
		last = state.get("bulk_date_last")
		if (title == last or not last):
			hold = False

		if hold:
			continue

		with open('wpbd_date.json', 'r') as f:
			data = json.load(f)

		print(title, url)
		output = parser.parse("nd", [url, "--onlymainspace"])
		data[title] = output
		state["bulk_date_last"] = title
		with open('wpbd_date.json', 'w') as f:
			json.dump(data, f, indent= 4)

		with open("state.json", 'w') as f:
			json.dump(state, f, indent = 4)

		parser.parse("cco", [])
		time.sleep(ctx.flags.delay)


@parser.command(name = "bulk_page_template_add", aliases = ["bpta"])
def cmd_bulk_page_template_add(ctx):
	with open("wpbd_date.json", 'r') as f:
		data = json.load(f)

	parser.parse("cco", [])
	hold = True
	for title, packet in sorted(list(data.items())):

		last = state.get("bulk_date_edit_last")
		if (title == last or not last):
			hold = False

		if hold:
			continue

		if (type(packet) == int):
			print(f"Skipping {title}")
			continue

		parser.parse("nd", [packet['url'], "--checkout", "--takelineprompt"])
		state["bulk_date_edit_last"] = title
		with open("state.json", 'w') as f:
			json.dump(state, f , indent= 4)


@parser.command(name = "bulk_page_template_report", aliases = ["bptr"])
def cmd_bulk_page_template_report(ctx):
	with open("wpbd_date.json", 'r') as f:
		data = json.load(f)

	total = 0
	ok = 0
	for (key, value) in data.items():
		if type(value) == int:
			ok += 1
		total += 1

	print(f"Total {total}, OK {ok}")

@parser.add_flag(name="nologin", value=False)
@parser.command(name="checkout", aliases=["co"])
def cmd_checkout(ctx):
    if not state.get('checkout'):
        print("No checkout to apply.")
        return

    page_url, new_text, summary = state['checkout']
    page = get_page_from_url(page_url)

    if not page.exists():
        print(f"Cannot find page - {page_url}")
        state['checkout'] = None
        return

    # Copy new text to clipboard
    pyperclip.copy(new_text)

    # Build edit URL (source editor) with summary prefilled
    base = "https://en.wikipedia.org/w/index.php"
    params = {
        "title": page.title(),
        "action": "edit",
        "summary": summary
    }

    edit_url = base + "?" + urllib.parse.urlencode(params)

    # Open edit page in new tab
    webbrowser.open_new_tab(edit_url)

    state['checkout'] = None
    print("Edit page opened. New text copied to clipboard. Paste and submit manually.")


@parser.command(name= "clearcheckout", aliases = ["cco"])
def cmd_clear_checkout(ctx):
	state['checkout'] = None

@parser.add_flag(name = "delay", type = float, default = 0)
@parser.command(name = "repeat", aliases = ["rep"])
def cmd_repeat_cmd(ctx, cmd):
	argv = shlex.split(cmd)

	target = argv[0]
	args = argv[1:]

	while True:
		ctx.parser.parse(target, args)
		time.sleep(ctx.flags.delay)

if __name__ == "__main__":
	parser.run()
	with open("state.json", 'w') as f:
		json.dump(state, f, indent = 4)