from datetime import datetime
import re

from scrapy.conf import settings
from scrapy.spider import BaseSpider
from scrapy.http import Request
from scrapy.selector import HtmlXPathSelector

from onderwijsinspectie.items import EducationalInstitution


class OWINSPSpider(BaseSpider):
    name = 'toezichtkaart.owinsp.nl'

    # The insane amount of form data that is send when searching for schools
    search_form_data = [('xl', '0'), ('sector', '#'), ('sch_id', '-1'),
        ('arr_id', '-1'), ('p1', '#'), ('p2', 'curpg'), ('p3', ),
        ('p1', '#'), ('p2', 'maxpg'), ('p3', '0'), ('p1', '#'), ('p2', 'hits'),
        ('p3', '0'), ('p1', 'sector'), ('p2', '='), ('p1', 'naam'),
        ('p2', '='), ('p1', 'plaats'), ('p2', '='), ('p3', 'PO'), ('p3', ''),
        ('p3', 'Rotterdam'), ('p1', '#'), ('p2', 'submit'), ('p3', 'Zoeken')]

    def generate_search_url(self, zips=settings['ZIPCODES'],
                    education_sector=settings['EDUCATION_SECTOR']):
        search_urls = []
        with open(zips, 'r') as f:
            for line in f:
                # Generate the ridiculous search URL
                search_urls.append('http://toezichtkaart.owinsp.nl/schoolwijzer/'\
                     'zoekresultaat?xl=0&p1=%%23&p2=maxpg&p3=-1&p1=%%23'\
                     '&p2=hits&p3=-1&p1=sector&p2=%%3D'\
                     '&p3=%(education_sector)s&p1=naam&p2=%%3D&p3=&p1=postcode'\
                     '&p2=%%3D&p3=%(zipcode)s&p1=%%23&p2=submit&p3=Zoeken'\
                     '&p1=%%23&p2=curpg&p3=1'\
                     % {'education_sector': education_sector, 'zipcode':\
                        line.strip()})

        return search_urls

    def start_requests(self):
        return [Request(url, self.parse_search_results) for url in\
                    self.generate_search_url()]

    def parse_search_results(self, response):
        hxs = HtmlXPathSelector(response)

        # Extract the link to the detail page of each school
        search_hits = hxs.select('//li[@class="match"]/noscript/a/@href')
        if not search_hits:
            return

        for link in search_hits:
            url = 'http://toezichtkaart.owinsp.nl/schoolwijzer/%s'\
                % link.extract()

            # First parse education strucure page if we are crawling 'VO'
            # ('voortgezet onderwijs') institutions
            if settings['EDUCATION_SECTOR'] == 'VO':
                yield Request(url, self.parse_education_structure_page)
            else:
                yield Request(url, self.follow_closed_blocks)

        pages = hxs.select('//span[@class="pagnr"]/text()').extract()[0]\
                            .strip()
        current_page, total_pages = re.compile(r"^Pagina (\d+) van (\d+)$")\
                                                .search(pages).groups()

        if current_page != total_pages:
            nav_urls = hxs.select('//span[@class="browse"]/noscript//a')
            if len(nav_urls) > 1:
                next_page = nav_urls[1].select('@href').extract()
            else:
                next_page = nav_urls[0].select('@href').extract()

            yield Request('http://toezichtkaart.owinsp.nl/schoolwijzer/%s'
                % next_page[0], self.parse_search_results)

    def parse_education_structure_page(self, response):
        hxs = HtmlXPathSelector(response)
        for structure in hxs.select('//li[@class="match"]/noscript/a'):
            url = 'http://toezichtkaart.owinsp.nl/schoolwijzer/%s'\
                % structure.select('@href').extract()[0]
            request = Request(url, self.parse_organisation_detail_page)
            request.meta['item'] = structure.select('text()').extract()[0]

            yield request

    def follow_closed_blocks(self, response):
        hxs = HtmlXPathSelector(response)
        tzk = hxs.select('//div[@class="content_main wide"]/div[@class="tzk"]'
            '/div/ul/li[@class="closed"]/noscript/a')

        # If no (closed) TZK links are found, pass the response to the
        # organisation detail parser
        if tzk:
            req = Request('http://toezichtkaart.owinsp.nl/schoolwijzer/%s'\
                % tzk.select('@href').extract()[0],
                self.parse_organisation_detail_page)

            if 'item' in response.meta:
                req.meta['item'] = response.meta['item']

            yield req
        else:
            yield Request(response.url, self.parse_organisation_detail_page)

    def parse_organisation_detail_page(self, response):
        hxs = HtmlXPathSelector(response)

        organisation = EducationalInstitution()

        h_content = hxs.select('//div[@id="hoofd_content"]')
        organisation['name'] = h_content.select('h1[@class="stitle"]/text()')\
                            .extract()[0].strip()

        address = h_content.select('./p[@class="detpag"]/text()').extract()
        address = ', '.join([x.strip() for x in address])
        organisation['address'] = address.replace(u'\xa0\xa0', u' ')
        website = h_content.select('ul/li[@class="actlink"]/a/@href').extract()
        if website:
            organisation['website'] = website[0]

        organisation['denomination'] = h_content.select('p/em/text()')\
                            .extract()[0].strip()
        organisation['rating_excerpt'] = h_content.select('p[3]/text()')\
                            .extract()[1].strip()

        # Wait... what? Are we going to use an element's top-padding to
        # get the div we are interested in? Yes we are :(.
        tzk_rating = hxs.select('//div[@class="content_main wide" and '
                         '@style="padding-top:0px"]/div[@class="tzk"]')
        organisation['rating'] = tzk_rating.select('div/text()').extract()[0]
        r_date = tzk_rating.select('h3/div/text()')\
                            .extract()[0].replace('\n', ' ').strip()[-10:]
        organisation['rating_date'] = '%s-%s-%s' % (r_date[-4:], r_date[3:5],
            r_date[0:2])

        reports = hxs.select('//div[@class="blocknone"]//div[@class="report"]'
            '//a')
        if reports:
            organisation['reports'] = []

            for report in reports:
                title = report.select('text()').extract()[0]
                date, title = title.split(': ')
                try:
                    # Try to parse date
                    date = datetime.strptime(date, '%d-%m-%Y')\
                            .strftime('%Y-%m-%d')
                except:
                    print '=' * 80
                    print date
                    print
                    pass

                organisation['reports'].append({
                    'url': 'http://toezichtkaart.owinsp.nl/schoolwijzer/%s'\
                        % report.select('@href').extract()[0],
                    'title': title.strip(),
                    'date': date
                })

        rating_history = hxs.select('//div[@class="blocknone"]'
            '//div[@class="report"]//li/text()').extract()
        if rating_history:
            organisation['rating_history'] = []

            for ratingstring in rating_history:
                date, rating = ratingstring.split(': ')
                try:
                    # Try to parse date
                    date = datetime.strptime(date, '%d-%m-%Y')\
                            .strftime('%Y-%m-%d')
                except:
                    pass

                organisation['rating_history'].append({
                    'rating': rating.strip(),
                    'date': date
                })

        organisation['education_sector'] = settings['EDUCATION_SECTOR'].lower()

        if 'item' in response.meta:
            organisation['education_structure'] = response.meta['item']

        organisation['owinsp_url'] = response.url

        yield organisation
