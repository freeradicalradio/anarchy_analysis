# anarchy_analysis
Scraping content from NA anarchist sites, making the data accessible, and doing some minor analysis


## What's the code doing?
At a high level, it crawls through directory pages finding links to articles, and then pulls out the content and some basic metadata for each article. It does this for Crimethinc, IGD, and Anews.
* `CONFIG` defines the config for crawling each of IGD, anews, and crimethinc. The `url_format` is a format string for the directory pagesthat can be formatted with an incremented integer to get to all of the content on the site. The `extractor` function defines how to get all of the posts referenced at that one url.
* `extractor` functions work differently based onthe site. Crimethinc is convenient enough to have a paginated RSS feed that has the full content in it. Parsing RSS feeds is a solved problem, so this makes crawling Crimethinc easy. IGD and anews don't have paginating rss feeds, so each of them also have functions for gettings all the links to content from a directory page, and getting all the content+metadata from a single post url.
* Outside of that, it iterates over each site and increments the paging index, saving the content. The data is then written out as csvs with headers and the deafult python csv writer csv dialect: [[see here for details]](https://docs.python.org/2/library/csv.html#csv-fmt-params)
* Next it moves to the analysis. If you have the data crawled already, at this point you can run the script with `--crawl-state=<file>` pointing to the crawl output csv, and it will start with that instead of crawling again. Currently the analysis acts on a whole site's corpus and finds the number of sentences, words, then filters down to meaningful words, finds them number of unique words, and then does a top N most frequent words frequency distribution. This data is then outputted as json.
