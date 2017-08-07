#!/usr/bin/env python3
"""Crawls and parses a handful of anarchist media sites."""

# TODO rename file
import nltk
import nltk.corpus
import parse
import sys
import argparse
import feedparser
import nltk.data
import csv
import re
import collections
import json
import dateutil.parser
import retrying
import time
import requests
import logging
import fake_useragent
from bs4 import BeautifulSoup

ua = fake_useragent.UserAgent()
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s;%(levelname)s;%(message)s")
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("parse").setLevel(logging.WARNING)
STOPWORDS = set(nltk.corpus.stopwords.words('english'))
CRAWL_STATE_FORMAT = "raw_posts_{}.csv"
ANALYSIS_FORMAT = "post_analysis_analysis_{}.json"
csv.field_size_limit(sys.maxsize)

MAX_PAGES = 1500  # How many pages of directory to crawl before giving up
# (title, body, date, url)


@retrying.retry(stop_max_attempt_number=10, wait_exponential_multiplier=2000, wait_random_max=30000)
def soup_with_retry(url):
    """Downloads the content at the url and loads it into BeautifulSoup with retrying."""
    logging.info("GET '%s'", url)
    r = requests.get(url, headers={"User-Agent": ua.random})
    if r.status_code != 200:
        logging.Warn("GET '%s' returned %s:%s", url, r.text)
        raise Exception()
    soup = BeautifulSoup(r.text, 'html5lib')
    return soup


def anews_get_links_from_directory_page(url):
    """Downloads and parses an anews directory page and returns the linked article urls"""
    soup = soup_with_retry(url)
    relative_links = [article.find('h2').find('a').get('href') for article in soup.find_all("article")]
    return ["http://anarchistnews.org" + rel for rel in relative_links]


def anews_get_content(url):
    """Downloads and parses an anews article, returning title, body, date, and link in a csv"""
    soup = soup_with_retry(url)
    title = soup.find('h1', {'class': 'page-title'}).get_text()
    body = soup.find(id='block-system-main').find('div', {'class': 'field-name-body'}).find('div', {'class': 'field-item'}).get_text()
    datestr = soup.find('span', {'property': 'dc:date dc:created'}).get('content')
    date = dateutil.parser.parse(datestr).isoformat()
    return (title, body, date, url)


def igd_get_links_from_directory_page(url):
    """Downloads and parses an igd directory page and returns the linked article urls"""
    soup = BeautifulSoup(requests.get(url, headers={"User-Agent": ua.random}).text, 'html.parser')
    headers = soup.find('div', {'class': 'td-pb-article-list'}).find_all('h3', {"class": "entry-title"})
    return [h.find('a').get('href') for h in headers]


def igd_get_content(url):
    """Downloads and parses an igd article, returning title, body, date, and link in a csv"""
    soup = soup_with_retry(url)
    title = soup.find("h1", {"class": "entry-title"}).get_text()
    date = soup.find('div', {'class': 'td-post-header'}).find('time').get('datetime')
    # $trips out the $ellout from the dom tree:
    soup.find('div', {'class': re.compile('patreon')}).extract()
    body = soup.find('div', {'class': 'td-post-content'}).get_text()
    return (title, body, dateutil.parser.parse(date).isoformat(), url)


def html_get_all_content_generator(links_from_directory, get_content):
    """Function generator. Generated function loops through the list of links found
    by `links_from_directory` and applies `get_content` to each, and returns the
    full list of all the content returned."""
    def get_all_content_in_directory_page(url):
        links = links_from_directory(url)
        all_content = []
        for link in links:
            try:
                all_content.append(get_content(link))
            except Exception as e:
                logging.error("Error getting/parsing %s, skipping: %s", url, e)
            time.sleep(1)
        return all_content
    return get_all_content_in_directory_page
igd_get_all_content_in_directory_page = html_get_all_content_generator(igd_get_links_from_directory_page, igd_get_content)
anews_get_all_content_in_directory_page = html_get_all_content_generator(anews_get_links_from_directory_page, anews_get_content)


def get_content_rss(url):
    """Pulls the title, published date, and body out of all rss elements in the given
    rss feed url and returns them in a list of rows [{title, published, body}, ...]"""
    f = feedparser.parse(url, agent=ua.random)
    to_ret = []
    for e in f.entries:
        soup = BeautifulSoup(e.content[0].value, 'html.parser')
        body = soup.get_text()
        to_ret.append(e.title, body, dateutil.parser.parse(e.published).isoformat(), e.link)
    return to_ret

SiteConfig = collections.namedtuple("SiteConfig", ["name", "url_format", "page_start", "extractor"])
CONFIG = [
    SiteConfig(name='crimethinc', url_format='https://crimethinc.com/feed?page={}',
               page_start=1, extractor=get_content_rss),
    SiteConfig(name="itsgoingdown", url_format='https://itsgoingdown.org/page/{}',
               page_start=1, extractor=igd_get_all_content_in_directory_page),
    SiteConfig(name='anarchistnews', url_format="http://anarchistnews.org/node?page={}",
               page_start=0, extractor=anews_get_all_content_in_directory_page)
]


def crawl_site(name, url_format, extractor, offset):
    """Crawls a website, returning the title, published datetime, and html
    content as a list of dicts.
    `url_format` is a format string representing the directory pages.
    `name` is the name of the site being crawled for logging
    `extractor` is the function that parses a directory page and returns all
    the content referenced in that page"""
    all_content = []
    prev_content = ''
    for i in range(offset, MAX_PAGES+offset):
        url = url_format.format(i)
        logging.info("crawling page %s of %s: '%s'", i+1-offset, name, url)
        content = extractor(url)
        time.sleep(1)
        if len(content) == 0 or content == prev_content:
            return all_content
        all_content.extend(content)
        prev_content = content
    logging.warn("reached MAX_PAGES(%s) when crawling %s so finished there", MAX_PAGES, name)
    return all_content


def crawl_all(start_time):
    """Crawls all the sites in the config, appending the name of the site to each row, writing them to
    a csv"""
# TODO refactor out the state writing into an outer function
# TODO do each site at the same time in parallel
    rows = []
    for cfg in CONFIG:
        logging.info("starting to crawl %s rss", cfg.name)
        raw_crawl = crawl_site(cfg.name, cfg.url_format, cfg.extractor, cfg.page_start)
        named = [(cfg.name,) + row for row in raw_crawl]
        rows.extend(named)
    with open(CRAWL_STATE_FORMAT.format(start_time), 'w', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['site', 'title', 'body', 'date', 'link'])
        writer.writerows(rows)
    return rows


def word_meaningful(word):
    """Returns true if the word is probably meaningful"""
    l = word.lower()
    return not (l.isnumeric() or l in STOPWORDS or len(l) < 2)


def analyze(corpus):
    """TODO
    input like [(source, title, body, date, link), ...]
    """
    logging.info("starting analysis")
    sentence_tokenizer = nltk.data.load('tokenizers/punkt/english.pickle')
    analysis = {}
    analysis['num_posts'] = len(corpus)
    sentences = []
    words = []
    for i, row in enumerate(corpus):
        if i % 100 == 0:
            logging.info("tokenizing row %s of %s", i+1, len(corpus))
        ss = sentence_tokenizer.tokenize(row[2])
        sentences.extend(ss)
        for s in ss:
            words.extend(nltk.word_tokenize(s))
    analysis['num_sentences'] = len(sentences)
    analysis['num_words'] = len(words)
    words = [w.lower() for w in words if word_meaningful(w)]
    analysis['num_meaningful_words'] = len(words)
    analysis['num_unique_words'] = len(set(words))

    fdist = nltk.FreqDist(words)
    analysis['freqDist'] = dict(fdist)
    logging.info("finished analysis")
    return analysis
    # collocations? word pairs that happen often: bigrams(abc)=ab,bc - .collocations()
    # bad words: https://github.com/ben174/profanity vs https://github.com/alvations/expletives
    # use nltk sentence tokenizer and feed that into markovify https://github.com/jsvine/markovify
    # this neural network? https://github.com/karpathy/char-rnn


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--crawl-state', help="file path for output from a crawl run to pick up from there instead of recrawling", type=str)
    args = parser.parse_args()
    if args.crawl_state:
        logging.info("reading crawl state from file")
        crawl_time = parse.parse(CRAWL_STATE_FORMAT, args.crawl_state)[0]
        with open(args.crawl_state, 'r') as f:
            reader = csv.reader(f)
            rows = [row for row in reader]
    else:
        crawl_time = int(time.time())
        rows = crawl_all()

    out = analyze(rows)
    with open(ANALYSIS_FORMAT.format(crawl_time), 'w') as f:
        json.dump(out, f)


# TODO: parse anews comments
# TODO: serve json at frr
# TODO: modify the apache server to let /api/* routes through
# parse into words, normalize.
# is there a library for this shit?

# TODO: set up error reporting somehow
# TODO: set up cron

if __name__ == "__main__":
    main()
