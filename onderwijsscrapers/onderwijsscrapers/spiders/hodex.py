from scrapy import log
from scrapy.spider import Spider
from scrapy.http import Request

from scrapy.contrib.spiders import CrawlSpider, Rule
from scrapy.contrib.spiders import XMLFeedSpider
from scrapy.contrib.linkextractors import LinkExtractor
from scrapy.contrib.linkextractors.lxmlhtml import LxmlLinkExtractor

from onderwijsscrapers.items import HodexHoProgramme

class HodexHoProgrammesSpider(Spider):
    name = 'hodex'
    allowed_domains = ['hodex.nl', 'hareapps.utwente.nl']
    start_urls = ['http://hareapps.utwente.nl/hodexdata/']
    #iterator = 'xml'
    itertag = 'hodexResource'
    orgUnitBrinMap = {
        "ut": "21PH",
        "uu": "21PD",
        "um": "21PJ",
        "hg": "25BE",
        "uvt": "21PN",
        "tue": "21PG",
        "lei": "21PB",
        "rug": "21PC",
        "run": "21PM",
        "uva": "21PK",
        "eur": "21PE",
        "wur": "21PI",
        "tud": "21PF",
        "vhl": "30HD",
        "vu": "21PL",
        "fontys": "30GB",
        "hro": "22OJ",
        "hhs": "27UM",
        "hva": "28DN",
        "nhtv": "21UI",
        "hu": "25DW",
        "artez": "27NF",
        "stenden": "22EX",
        "hku": "00MF",
        "avans": "07GR",
        "tias": "27ZC",
        "nyenrode": "01MC",
        "nhl": "21WN"
    }

    def start_requests(self):
        return [
            Request(
                'http://hareapps.utwente.nl/hodexdata/',
                self.parse_index
            )
        ]

    def parse_index(self, response):
        # /hodexDirectory/hodexResource
        sel = response.selector;
        sel.register_namespace('h', 'http://www.hodex.nl/hodexDirectory')
        sels = sel.xpath('//h:hodexDirectory/h:hodexResource/h:hodexResourceURL/text()')
        links = []
        # print sels
        for link in sels:
            links.append(Request(str(link.extract()), self.parse_info))

        return links

    def parse_info(self, response):
        print "hoi"
        return

    def parse_node(self, response, node):
        return item
