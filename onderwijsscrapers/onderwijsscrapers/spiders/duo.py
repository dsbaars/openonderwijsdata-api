import csv
import cStringIO
import locale
import xlrd
from datetime import datetime
from os import devnull
from os.path import basename
from zipfile import ZipFile
from collections import defaultdict
from itertools import islice

import requests
from scrapy import log
from scrapy.spider import BaseSpider
from scrapy.http import Request
from scrapy.selector import HtmlXPathSelector

from onderwijsscrapers.items import (DuoVoBoard, DuoVoSchool, DuoVoBranch,
                                     DuoPoBoard, DuoPoSchool, DuoPoBranch,
                                     DuoPaoCollaboration)

locale.setlocale(locale.LC_ALL, 'nl_NL.UTF-8')


class DuoSpider(BaseSpider):
    """ Duo spider """
    def __init__(self, add_local_table=None, url_filter=None, *args, **kwargs):
        self.url_filter = url_filter
        # a table named `<reference_date>.csv` in ISO 8601
        self.add_local_table = add_local_table

    def start_requests(self):
        return [
            Request(
                'http://data.duo.nl/organisatie/open_onderwijsdata/databestanden/' + url,
                # lambda self, response: self.parse_cvs(self, response, parse_row)
                parse_row
            ) for url,parse_row in self.requests.items() if (self.url_filter is None or url == self.url_filter)
        ]

    def dataset(self, response, make_item, dataset_name, parse_row):
        """
        Add a dataset to a DUO item
        parse_row should return a key (like the tuple (brin, branch_id))
        and one item of the dataset for this brin

        This is a SequenceSchema in validation

        Args:
           make_item (function): takes a key and returns a DUO item
           dataset_name (string): name of the dataset field
           parse_row (function): takes a row and returns a (key, dataset-item) tuple
        """

        # TODO: add local file loading, refactor student_flow

        for csv_url, reference_date in find_available_datasets(response).iteritems():
            reference_year = reference_date.year
            reference_date = str(reference_date)

            dataset = {}
            for row in parse_csv_file(csv_url):
                for key, value in parse_row(row):
                    if key not in dataset:
                        dataset[key] = []
                    dataset[key].append(value)

            for key, item in dataset.iteritems():
                if key is not None:
                    school = make_item(key)
                    school['reference_year'] = reference_year
                    school['%s_reference_url' % dataset_name] = csv_url
                    school['%s_reference_date' % dataset_name] = reference_date
                    school[dataset_name] = item
                    yield school



def find_available_datasets(response, extension='csv'):
    """ Get all URLS of files with a certain extension on the DUO page """
    hxs = HtmlXPathSelector(response)
    available_datasets = {}
    datasets = hxs.select('//tr[.//a[contains(@href, ".%s")]]' % extension)
    for dataset_file in datasets:
        ref_date = dataset_file.select('./td[1]/span/text()').extract()
        ref_date = datetime.strptime(ref_date[0], '%d %B %Y').date()

        dataset_url = dataset_file.select('.//a/@href').re(r'(.*\.%s)' % extension)[0]

        available_datasets['http://duo.nl%s' % dataset_url] = ref_date
    return available_datasets

def extract_csv_files(zip_url):
    zip_file = requests.get(zip_url)

    csv_files = []
    zfiles = ZipFile(cStringIO.StringIO(zip_file.content))
    for zfile in zfiles.filelist:
        for sheet in parse_xls_sheets_from_content(zfiles.read(zfile)).values():
            csv_files.append(sheet)

    return csv_files

def parse_xls_sheets_from_content(content):
    """ Download and parse CSV content """
    xls = cStringIO.StringIO(content)

    # Suppress warnings as the xls files are wrongly initialized.
    with open(devnull, 'w') as OUT:
        wb = xlrd.open_workbook(file_contents=xls.read(), logfile=OUT)

    sheets = {}
    for sheet_name in wb.sheet_names():
        sh = wb.sheet_by_name(sheet_name)
        data = []
        for rownum in xrange(sh.nrows):
            data.append(sh.row_values(rownum))
        data = [[unicode(x) for x in row] for row in data]
        data = [';'.join(row) for row in data]
        data = '\n'.join(data)

        sheets[sheet_name] = csv.DictReader(cStringIO.StringIO(data.encode('utf8')), delimiter=';')
    return sheets

def parse_xls_sheets_from_url(xls_url):
    """ Download and parse CSV file """
    xls_file = requests.get(xls_url)
    # don't specify encoding
    return parse_xls_sheets_from_content(xls_file.content)

def parse_csv_file(csv_url):
    """ Download and parse CSV file """
    csv_file = requests.get(csv_url)
    csv_file.encoding = 'cp1252'
    csv_file = csv.DictReader(cStringIO.StringIO(csv_file.content
                  .decode('cp1252').encode('utf8')), delimiter=';')
    # todo: is this a dict or an iterator? can we do (whitespace) preprocessing here?
    return csv_file

def int_or_none(value):
    """
        Try to make `value` an int. If the string is not a number,
        or empty, return None.
    """
    try:
        return int(value)
    except ValueError:
        return None

def extend_to_blank(l):
    """ Transform ['','a','','b',''] into ['','a','a','b','b'] """
    out, ext = [], ''
    for i in l:
        ext = i or ext
        out.append(ext)
    return out


def get_staff_people(xls_url, with_brin=True):
    """
    Primair/Voortgezet onderwijs > Personeel
    Parse "01. Onderwijspersoneel in aantal personen"

    Both on school and board level

    returns dict of `{(year, brin/board id): {staff dict}}`
    """
    staff_per_school = {}

    sheets = parse_xls_sheets_from_url(xls_url)
    for row in islice(sheets['per owtype-bestuur-brin-functie'], None):
        brinnr = row.pop('BRIN NUMMER', None).strip()
        boardnr = int(float(row.pop('BEVOEGD GEZAG', None)))

        item_id = None
        if with_brin and brinnr != 'bovenschools':
            item_id = brinnr
        if (not with_brin) and brinnr == 'bovenschools':
            item_id = boardnr

        if item_id is not None:
            fg = row.pop('FUNCTIEGROEP', None)
            staff_per_year = {}

            # loop over all rows, filtering key names and extracting year
            for key, val in row.items():
                key = key.split()
                if key[-1].isdigit() and len(key[-1]) == 4:

                    year = int(key[-1])
                    if year not in staff_per_year:
                        staff_per_year[year] = {}

                    # missing values are starred, for privacy reasons
                    if val != '*':
                        if key[:-1] == ['PERSONEN']:
                            staff_per_year[year]['staff'] = int(float(val or 0))

                        if key[:-1] == ['MANNEN']:
                            staff_per_year[year]['staff_male'] = int(float(val or 0))
                        if key[:-1] == ['VROUWEN']:
                            staff_per_year[year]['staff_female'] = int(float(val or 0))
                        if key[:-1] == ['GESLACHT', 'ONBEKEND']:
                            staff_per_year[year]['staff_gender_unknown'] = int(float(val or 0))

                        # mean ages: 0 means missing
                        if key[:-1] == ['GEMIDDELDE', 'LEEFTIJD']:
                            staff_per_year[year]['age_mean'] = float(val or 0) or None
                        if key[:-1] == ['GEMIDDELDE', 'LEEFTIJD', 'MAN']:
                            staff_per_year[year]['age_mean_male'] = float(val or 0) or None
                        if key[:-1] == ['GEMIDDELDE', 'LEEFTIJD', 'VROUW']:
                            staff_per_year[year]['age_mean_female'] = float(val or 0) or None

                        if key[:-1] == ['GEMIDDELDE', "FTE'S"]:
                            staff_per_year[year]['workload_mean'] = float(val or 0)

                        if key[:-1] == ['PERSONEN', 'IN', 'TIJDELIJKE', 'DIENST']:
                            staff_per_year[year]['staff_temp'] = int(float(val or 0))
                        if key[:-1] == ['PERSONEN', 'IN', 'VASTE', 'DIENST']:
                            staff_per_year[year]['staff_perm'] = int(float(val or 0))

                        if 'staff_workload' not in staff_per_year[year]:
                            staff_per_year[year]['staff_workload'] = []
                        if key[0] == 'PERSONEN' and key[-2] == "FTE'S":
                            key_range = '>%s' % key[-3] if key[1] == 'MEER' else ''.join(key[1:-2])
                            staff_per_year[year]['staff_workload'].append({
                                'range': key_range,
                                'count': int(float(val or 0)),
                            })

                        if 'staff_age' not in staff_per_year[year]:
                            staff_per_year[year]['staff_age'] = []
                        if key[0] == 'PERSONEN' and key[-2] == 'JAAR':
                            key_range = '>%s' % key[-3] if key[1] == 'OUDER' \
                                   else '<%s' % key[-3] if key[1] == 'JONGER' \
                                   else ''.join(key[1:-2])
                            staff_per_year[year]['staff_age'].append({
                                'range': key_range,
                                'count': int(float(val or 0)),
                            })
                        if key[:-1] == ['LEEFTIJD', 'ONBEKEND']:
                            staff_per_year[year]['staff_age'].append({
                                'range': '?',
                                'count': int(float(val or 0)),
                            })

            # add rows per function group
            for year, staff in staff_per_year.items():
                if (year, item_id) not in staff_per_school:
                    staff_per_school[(year, item_id)] = []

                staff_per_school[(year, item_id)].append({
                    'function_group': fg,
                    'staff': staff if staff['staff'] else {'staff': 0},
                })

    return staff_per_school

def get_staff_fte(xls_url, with_brin=True):
    """
    Primair/Voortgezet onderwijs > Personeel
    Parse "02. Onderwijspersoneel in aantal fte"

    Both on school and board level

    returns dict of `{(year, brin/board id): {staff dict}}`
    """
    fte_per_school = {}

    sheets = parse_xls_sheets_from_url(xls_url)
    for row in islice(sheets['per owtype-bestuur-brin-functie'], None):
        brinnr = row.pop('BRIN NUMMER', None).strip()
        boardnr = int(float(row.pop('BEVOEGD GEZAG', None)))

        item_id = None
        if with_brin and brinnr != 'bovenschools':
            item_id = brinnr
        if (not with_brin) and brinnr == 'bovenschools':
            item_id = boardnr

        if item_id is not None:
            fg = row.pop('FUNCTIEGROEP', None)
            fte_per_year = {}

            # loop over all rows, filtering key names and extracting year
            for key, val in row.items():
                key = key.split()
                if key[-1].isdigit() and len(key[-1]) == 4:

                    year = int(key[-1])
                    if year not in fte_per_year:
                        fte_per_year[year] = {}

                    # missing values are starred, for privacy reasons
                    if val != '*':
                        if key[:-1] == ["FTE'S"]:
                            fte_per_year[year]['fte'] = float(val or 0)

                        if key[:-1] == ["FTE'S", 'MANNEN']:
                            fte_per_year[year]['fte_male'] = float(val or 0)
                        if key[:-1] == ["FTE'S", 'VROUWEN']:
                            fte_per_year[year]['fte_female'] = float(val or 0)
                        if key[:-1] == ["FTE'S", 'GESLACHT', 'ONBEKEND']:
                            fte_per_year[year]['fte_gender_unknown'] = float(val or 0)

                        # These fields are already present in staff_people
                        # mean ages: 0 means missing
                        # if key[:-1] == ['GEMIDDELDE', 'LEEFTIJD']:
                        #     fte_per_year[year]['age_mean'] = float(val or 0) or None
                        # if key[:-1] == ['GEMIDDELDE', 'LEEFTIJD', 'MAN']:
                        #     fte_per_year[year]['age_mean_male'] = float(val or 0) or None
                        # if key[:-1] == ['GEMIDDELDE', 'LEEFTIJD', 'VROUW']:
                        #     fte_per_year[year]['age_mean_female'] = float(val or 0) or None
                        # if key[:-1] == ['GEMIDDELDE', "FTE'S"]:
                        #     fte_per_year[year]['workload_mean'] = float(val or 0)

                        if key[:-1] == ["FTE'S", 'PERSONEN', 'IN', 'TIJDELIJKE', 'DIENST']:
                            fte_per_year[year]['fte_temp'] = float(val or 0)
                        if key[:-1] == ["FTE'S", 'PERSONEN', 'IN', 'VASTE', 'DIENST']:
                            fte_per_year[year]['fte_perm'] = float(val or 0)

                        if 'fte_workload' not in fte_per_year[year]:
                            fte_per_year[year]['fte_workload'] = []
                        if key[0:2] == ["FTE'S", 'PERSONEN'] and key[-2] == "FTE'S":
                            key_range = '>%s' % key[-3] if key[2] == 'MEER' else ''.join(key[2:-2])
                            fte_per_year[year]['fte_workload'].append({
                                'range': key_range,
                                'fte': float(val or 0),
                            })

                        if 'fte_age' not in fte_per_year[year]:
                            fte_per_year[year]['fte_age'] = []
                        if key[0:2] == ["FTE'S", 'PERSONEN'] and key[-2] == 'JAAR':
                            key_range = '>%s' % key[-3] if key[2] == 'OUDER' \
                                   else '<%s' % key[-3] if key[2] == 'JONGER' \
                                   else ''.join(key[2:-2])
                            fte_per_year[year]['fte_age'].append({
                                'range': key_range,
                                'fte': float(val or 0),
                            })
                        if key[:-1] == ["FTE'S", 'LEEFTIJD', 'ONBEKEND']:
                            fte_per_year[year]['fte_age'].append({
                                'range': '?',
                                'fte': float(val or 0),
                            })

            # add rows per function group
            for year, staff in fte_per_year.items():
                if (year, item_id) not in fte_per_school:
                    fte_per_school[(year, item_id)] = []

                fte_per_school[(year, item_id)].append({
                    'function_group': fg,
                    'fte': staff if staff['fte'] else {'fte': 0},
                })

    return fte_per_school

def get_student_flow(table, for_po=True):
    """
    Stroominformatie > Doorstromers
    05. Doorstromers van primair naar voortgezet onderwijs

    both relevant for po and vo
    """
    table = csv.DictReader(islice(table, 2, None), delimiter=';')
    table.fieldnames = [
        'BRIN NUMMER PO',
        'VESTIGINGSNUMMER PO',
        'INSTELLINGSNAAM PO',
        'STRAATNAAM PO',
        'PLAATSNAAM PO',
        'BRIN NUMMER VO',
        'VESTIGINGSNUMMER VO',
        'INSTELLINGSNAAM VO',
        'STRAATNAAM VO',
        'PLAATSNAAM VO',
        'AANTAL DOORSTROMERS',
    ]

    flow_per_branch = {}
    for row in table:
        po_brin = row['BRIN NUMMER PO']
        po_branch_id = int(row['VESTIGINGSNUMMER PO'])
        vo_brin = row['BRIN NUMMER VO']
        vo_branch_id = int(row['VESTIGINGSNUMMER VO'])
        if for_po:
            if (po_brin, po_branch_id) not in flow_per_branch:
                flow_per_branch[(po_brin, po_branch_id)] = []
            flow_per_branch[(po_brin, po_branch_id)].append({
                'to_vo_brin': vo_brin,
                'to_vo_branch_id': vo_branch_id,
                'out_flow': int(row['AANTAL DOORSTROMERS']),
            })
        else:
            if (vo_brin, vo_branch_id) not in flow_per_branch:
                flow_per_branch[(vo_brin, vo_branch_id)] = []
            flow_per_branch[(vo_brin, vo_branch_id)].append({
                'from_po_brin': po_brin,
                'from_po_branch_id': po_branch_id,
                'in_flow': int(row['AANTAL DOORSTROMERS']),
            })

    return flow_per_branch


class DuoVoBoardsSpider(DuoSpider):
    name = 'duo_vo_boards'

    def __init__(self, *args, **kwargs):
        self.make_item = lambda board_id: DuoVoBoard(board_id=board_id)
        self.requests = {
            'vo/adressen/Adressen/besturen.asp':
                self.parse_boards,
            'vo/Financien/Financien/Kengetallen.asp':
                self.parse_financial_key_indicators,
            'vo/leerlingen/Leerlingen/vo_leerlingen4.asp':
                self.parse_vavo_students,
            'vo/personeel/Personeel/vo_personeel_personen.asp':
                self.parse_vo_staff_people,
            'vo/personeel/Personeel/vo_personeel_fte.asp':
                self.parse_vo_staff_fte,
        }
        DuoSpider.__init__(self, *args, **kwargs)

    def parse_boards(self, response):
        """
        Parse "03. Adressen bevoegde gezagen"
        """
        for csv_url, reference_date in find_available_datasets(response).iteritems():
            reference_year = reference_date.year
            reference_date = str(reference_date)
            for row in parse_csv_file(csv_url):
                # strip leading and trailing whitespace.
                for key in row.keys():
                    row[key] = row[key].strip()

                board = DuoVoBoard()
                board['board_id'] = int(row['BEVOEGD GEZAG NUMMER'])
                board['name'] = row['BEVOEGD GEZAG NAAM']
                board['address'] = {
                    'street': '%s %s' % (row['STRAATNAAM'],
                                         row['HUISNUMMER-TOEVOEGING']),
                    'zip_code': row['POSTCODE'].replace(' ', ''),
                    'city': row['PLAATSNAAM']
                }

                board['correspondence_address'] = {}
                if row['STRAATNAAM CORRESPONDENTIEADRES']:
                    board['correspondence_address']['street'] = '%s %s'\
                        % (row['STRAATNAAM CORRESPONDENTIEADRES'],
                           row['HUISNUMMER-TOEVOEGING CORRESPONDENTIEADRES'])
                else:
                    board['correspondence_address']['street'] = None

                board['correspondence_address']['zip_code'] = row[
                        'POSTCODE CORRESPONDENTIEADRES'].replace(' ', '') or None

                board['correspondence_address']['city'] = row[
                        'PLAATSNAAM CORRESPONDENTIEADRES'] or None

                board['municipality'] = row['GEMEENTENAAM'] or None

                board['municipality_code'] = int_or_none(row['GEMEENTENUMMER'])

                board['phone'] = row['TELEFOONNUMMER'] or None

                board['website'] = row['INTERNETADRES'] or None

                board['denomination'] = row['DENOMINATIE'] or None

                board['administrative_office_id'] = int_or_none(row['ADMINISTRATIEKANTOORNUMMER'])

                board['reference_year'] = reference_year
                board['ignore_id_fields'] = ['reference_year']
                yield board

    def parse_financial_key_indicators(self, response):
        """
        Parse "15. Kengetallen"
        """

        indicators_mapping = {
            'LIQUIDITEIT (CURRENT RATIO)': 'liquidity_current_ratio',
            'RENTABILITEIT': 'profitability',
            'SOLVABILITEIT 1': 'solvency_1',
            'SOLVABILITEIT 2': 'solvency_2',
            'ALGEMENE RESERVE / TOTALE BATEN': 'general_reserve_div_total_income',
            'BELEGGINGEN (T.O.V. EV)': 'investments_relative_to_equity',
            'CONTRACTACTIVITEITEN / RIJKSBIJDRAGE': 'contract_activities_div_gov_funding',
            'CONTRACTACTIVITEITEN / TOTALE BATEN': 'contractactivities_div_total_profits',
            'EIGEN VERMOGEN / TOTALE BATEN': 'equity_div_total_profits',
            'INVESTERING HUISVESTING / TOTALE BATEN': 'housing_investment_div_total_profits',
            'INVESTERINGEN (INVENT.+APP.) / TOTALE BATEN': 'investments_div_total_profits',
            'KAPITALISATIEFACTOR': 'capitalization_ratio',
            'LIQUIDITEIT (QUICK RATIO)': 'liquidity_quick_ratio',
            'OV. OVERHEIDSBIJDRAGEN / TOT. BATEN': 'other_gov_funding_div_total_profits',
            'PERSONEEL / RIJKSBIJDRAGEN': 'staff_costs_div_gov_funding',
            'PERSONELE LASTEN / TOTALE LASTEN': 'staff_expenses_div_total_expenses',
            'RIJKSBIJDRAGEN / TOTALE BATEN': 'gov_funding_div_total_profits',
            'VOORZIENINGEN /TOTALE BATEN': 'facilities_div_total_profits',
            #'WEERSTANDSVERMOGEN (-/- MVA)': '',
            #'WEERSTANDSVERMOGEN VO TOTALE BN.': '',
            'WERKKAPITAAL / TOTALE BATEN': 'operating_capital_div_total_profits',
            'HUISVESTINGSLASTEN / TOTALE LASTEN': 'housing_expenses_div_total_expenses',
            'WERKKAPITAAL': 'operating_capital',
        }

        def parse_row(row):
            # strip leading and trailing whitespace.
            for key in row.keys():
                row[key] = row[key].strip()
            # Don't look at the 'Kengetallen per sector' file
            if 'BEVOEGD GEZAG NUMMER' in row:
                board_id = int(row['BEVOEGD GEZAG NUMMER'])

                indicators = {}
                indicators['year'] = int(row['JAAR'])
                indicators['group'] = row['GROEPERING']

                for ind, ind_norm in indicators_mapping.iteritems():
                    indicators[ind_norm] = float(row[ind].replace('.', '')
                                                         .replace(',', '.'))

                yield board_id, indicators

        return self.dataset(response, self.make_item, 'financial_key_indicators_per_year', parse_row)

    def parse_vavo_students(self, response):
        """
        Primair onderwijs > Leerlingen
        Parse "04 Leerlingen per bestuur en denominatie (vavo apart)"
        """

        def parse_row(row):
            board_id = int(row['BEVOEGD GEZAG NUMMER'].strip())
            vavo_students = {
                'non_vavo' : int(row['AANTAL LEERLINGEN'] or 0),
                'vavo' : int(row['AANTAL VO LEERLINGEN UITBESTEED AAN VAVO'] or 0),
            }
            yield board_id, vavo_students

        return self.dataset(response, self.make_item, 'vavo_students', parse_row)

    def parse_vo_staff_people(self, response):
        """
        Voortgezet onderwijs > Personeel
        Parse "01. Onderwijspersoneel in aantal personen"
        """

        for xls_url, reference_date in find_available_datasets(response, extension='xls').iteritems():
            reference_year = reference_date.year # different years in document
            reference_date = str(reference_date)
            staff_per_board = get_staff_people(xls_url, with_brin=False)

            for (year, board_id), per_board in staff_per_board.iteritems():
                board = DuoVoBoard(
                    board_id=board_id,
                    reference_year=year,
                    staff_reference_url=xls_url,
                    staff_reference_date=reference_date,
                    staff=per_board
                )
                yield board

    def parse_vo_staff_fte(self, response):
        """
        Voortgezet onderwijs > Personeel
        Parse "02. Onderwijspersoneel in aantal fte"
        """

        for xls_url, reference_date in find_available_datasets(response, extension='xls').iteritems():
            reference_year = reference_date.year # different years in document
            reference_date = str(reference_date)
            staff_per_board = get_staff_fte(xls_url, with_brin=False)

            for (year, board_id), per_board in staff_per_board.iteritems():
                board = DuoVoBoard(
                    board_id=board_id,
                    reference_year=year,
                    fte_reference_url=xls_url,
                    fte_reference_date=reference_date,
                    fte=per_board
                )
                yield board

class DuoVoSchoolsSpider(DuoSpider):
    name = 'duo_vo_schools'

    def __init__(self, *args, **kwargs):
        self.make_item = lambda brin: DuoVoSchool(brin=brin)
        self.requests = {
            'vo/adressen/Adressen/hoofdvestigingen.asp':
                self.parse_schools,
            'vschoolverlaten/vsv_voortgezet.asp':
                self.parse_dropouts,
            'vo/leerlingen/Leerlingen/vo_leerlingen11.asp':
                self.parse_students_prognosis,
            'passendow/Adressen/Adressen/passend_vo_6.asp':
                self.parse_vo_lo_collaboration,
            'passendow/Adressen/Adressen/passend_vo_8.asp':
                self.parse_pao_collaboration,
            'vo/personeel/Personeel/vo_personeel_personen.asp':
                self.parse_vo_staff_people,
            'vo/personeel/Personeel/vo_personeel_fte.asp':
                self.parse_vo_staff_fte,
            'vo/personeel/Personeel/vo_personeel_3.asp':
                self.parse_vo_staff_course,
            'vo/personeel/Personeel/vo_personeel_4.asp':
                self.parse_vo_time_per_course,
        }
        DuoSpider.__init__(self, *args, **kwargs)

    def parse_schools(self, response):
        """
        Parse: "01. Adressen hoofdvestigingen"
        """

        # Fields that do not need additonal processing
        school_fields = {
            'BRIN NUMMER': 'brin',
            'PROVINCIE': 'province',
            'INSTELLINGSNAAM': 'name',
            'GEMEENTENAAM': 'municipality',
            'DENOMINATIE': 'denomination',
            'INTERNETADRES': 'website',
            'TELEFOONNUMMER': 'phone',
            'ONDERWIJSGEBIED NAAM': 'education_area',
            'NODAAL GEBIED NAAM': 'nodal_area',
            'RPA-GEBIED NAAM': 'rpa_area',
            'WGR-GEBIED NAAM': 'wgr_area',
            'COROPGEBIED NAAM': 'corop_area',
            'RMC-REGIO NAAM': 'rmc_region'
        }

        for csv_url, reference_date in find_available_datasets(response).iteritems():
            reference_year = reference_date.year
            reference_date = str(reference_date)
            for row in parse_csv_file(csv_url):
                # strip leading and trailing whitespace.
                for key in row.keys():
                    value = row[key].strip()
                    row[key] = value or None

                school = DuoVoSchool()
                school['board_id'] = int(row['BEVOEGD GEZAG NUMMER'])
                school['address'] = {
                    'street': '%s %s' % (row['STRAATNAAM'],
                                         row['HUISNUMMER-TOEVOEGING']),
                    'city': row['PLAATSNAAM'],
                    'zip_code': row['POSTCODE'].replace(' ', '')
                }

                school['correspondence_address'] = {
                    'street': '%s %s' % (row['STRAATNAAM CORRESPONDENTIEADRES'],
                                         row['HUISNUMMER-TOEVOEGING '
                                             'CORRESPONDENTIEADRES']),
                    'city': row['PLAATSNAAM CORRESPONDENTIEADRES'],
                    'zip_code': row['POSTCODE CORRESPONDENTIEADRES']
                }

                school['municipality_code'] = int_or_none(row['GEMEENTENUMMER'])
                school['education_structures'] = row['ONDERWIJSSTRUCTUUR']
                if school['education_structures']:
                    school['education_structures'] = school['education_structures'].split('/')

                if row['COROPGEBIED CODE']:
                    school['corop_area_code'] = int(row['COROPGEBIED CODE'])

                school['nodal_area_code'] = int_or_none(row['NODAAL GEBIED CODE'])

                school['rpa_area_code'] = int_or_none(row['RPA-GEBIED CODE'])

                school['wgr_area_code'] = int_or_none(row['WGR-GEBIED CODE'])

                school['education_area_code'] = int_or_none(row['ONDERWIJSGEBIED CODE'])

                school['rmc_region_code'] = int_or_none(row['RMC-REGIO CODE'])

                for field, field_norm in school_fields.iteritems():
                    school[field_norm] = row[field]

                school['reference_year'] = reference_year
                school['ignore_id_fields'] = ['reference_year']

                yield school

    def parse_dropouts(self, response):
        """
        Parse: "02. Vsv in het voortgezet onderwijs per vo instelling"
        """

        def parse_row(row):
            # strip leading and trailing whitespace and remove
            # thousands separator ('.')
            for key in row.keys():
                row[key] = row[key].strip().replace('.', '')

            brin = row['BRIN NUMMER']

            dropouts = {
                'year': int(row['JAAR']),
                'education_structure': row['ONDERWIJSSTRUCTUUR EN LEERJAAR'],
                'total_students': int(row['AANTAL DEELNEMERS']),
                'total_dropouts': int(row['AANTAL VSV ERS']),
                'dropouts_with_vmbo_diploma': int(row['AANTAL VSV ERS MET VMBO DIPLOMA']),
                'dropouts_with_mbo1_dimploma': int(row['AANTAL VSV ERS MET MBO1 DIPLOMA']),
                'dropouts_without_diploma': int(row['AANTAL VSV ERS ZONDER DIPLOMA'])
            }

            if row['PROFIEL'] == 'NVT':
                dropouts['sector'] = None
            else:
                dropouts['sector'] = row['PROFIEL']

            yield brin, dropouts

        return self.dataset(response, self.make_item, 'dropouts_per_year', parse_row)

    def parse_students_prognosis(self, response):
        """
        Parse: "11. Prognose aantal leerlingen"
        """

        def parse_row(row):
            # strip leading and trailing whitespace and remove
            # thousands separator ('.')
            for key in row.keys():
                row[key] = row[key].strip().replace('.', '')

            brin = row['BRIN NUMMER']

            # ignoring actual data for now, only adding prognosis
            students_prognosis = []
            for k, v in row.iteritems():
                row_words = k.split()
                # k is of the form 'PROGNOSE LWOO PRO 2024'
                if row_words[0] == 'PROGNOSE' or row_words[0]=='POGNOSE': # don't ask
                    if v and row_words[-1].isdigit():
                        yield brin, {
                            'year' : int(row_words[-1]),
                            'structure' : '_'.join(row_words[1:-1]).lower(),
                            'students' : int(v),
                        }

        return self.dataset(response, self.make_item, 'students_prognosis', parse_row)

    def parse_vo_lo_collaboration(self, response):
        """
        Passend onderwijs > Adressen
        Parse "06. Adressen instellingen per samenwerkingsverband lichte ondersteuning, voortgezet onderwijs"
        """

        def parse_row(row):
            # strip leading and trailing whitespace.
            for key in row.keys():
                value = (row[key] or '').strip()
                row[key] = value or None
                row[key.strip()] = value or None

            if row.has_key('BRINNUMMER'):
                row['BRIN NUMMER'] = row['BRINNUMMER']

            brin = row['BRIN NUMMER']
            cid = row['ADMINISTRATIENUMMER'].strip()
            if '-' in cid:
                int_parts = map(int_or_none, cid.split('-'))
                if any([i == None for i in int_parts]):
                    cid = '-'.join(map(str, int_parts))
            collaboration = cid

            yield brin, collaboration

        return self.dataset(response, self.make_item, 'vo_lo_collaboration', parse_row)

    def parse_pao_collaboration(self, response):
        """
        Passend onderwijs > Adressen
        Parse "08. Adressen instellingen per samenwerkingsverband passend onderwijs, voortgezet onderwijs"
        """

        def parse_row(row):
            # strip leading and trailing whitespace.
            for key in row.keys():
                value = (row[key] or '').strip()
                row[key] = value or None
                row[key.strip()] = value or None

            if row.has_key('BRINNUMMER'):
                row['BRIN NUMMER'] = row['BRINNUMMER']

            brin = row['BRIN NUMMER']
            collaboration = row['ADMINISTRATIENUMMER']
            yield brin, collaboration

        return self.dataset(response, self.make_item, 'pao_collaboration', parse_row)

    def parse_vo_staff_people(self, response):
        """
        Voortgezet onderwijs > Personeel
        Parse "01. Onderwijspersoneel in aantal personen"
        """

        for xls_url, reference_date in find_available_datasets(response, extension='xls').iteritems():
            reference_year = reference_date.year # different years in document
            reference_date = str(reference_date)
            staff_per_school = get_staff_people(xls_url, with_brin=True)

            for (year, brin), per_school in staff_per_school.iteritems():
                school = DuoVoSchool(
                    brin=brin,
                    reference_year=year,
                    staff_reference_url=xls_url,
                    staff_reference_date=reference_date,
                    staff=per_school
                )
                yield school

    def parse_vo_staff_fte(self, response):
        """
        Voortgezet onderwijs > Personeel
        Parse "02. Onderwijspersoneel in aantal fte"
        """

        for xls_url, reference_date in find_available_datasets(response, extension='xls').iteritems():
            reference_year = reference_date.year # different years in document
            reference_date = str(reference_date)
            staff_per_school = get_staff_fte(xls_url, with_brin=True)

            for (year, brin), per_school in staff_per_school.iteritems():
                school = DuoVoSchool(
                    brin=brin,
                    reference_year=year,
                    fte_reference_url=xls_url,
                    fte_reference_date=reference_date,
                    fte=per_school
                )
                yield school

    def parse_vo_staff_course(self, response):
        """
        Voortgezet onderwijs > Personeel
        Parse "03. Onderwijspersoneel in aantal personen per vak"
        """
        INTS = {
            'staff':
                ['AANTAL','PERSONEN'],
            'staff_perm':
                ['AANTAL','PERSONEN','IN','VASTE','DIENST'],
            'staff_temp':
                ['AANTAL','PERSONEN','IN','TIJDELIJKE','DIENST'],
            'staff_substitutes':
                ['AANTAL','PERSONEN','IN','DIENST','ALS','VERVANGING'],
            'staff_unappointed':
                ['AANTAL','PERSONEN','IN','DIENST','ZONDER','BENOEMING'],
            'staff_appointment_unknown':
                ['AANTAL','PERSONEN','IN','DIENST','ALS','ONBEKEND'],
            'staff_female':
                ['VROUWEN'],
            'staff_male':
                ['MANNEN'],
            'staff_gender_unknown':
                ['GESLACHT','ONBEKEND'],
        }
        FLOATS = {
            'age_mean':
                ['GEMIDDELDE','LEEFTIJD'],
            'age_mean_female':
                ['GEMIDDELDE','LEEFTIJD','VROUW'],
            'age_mean_male':
                ['GEMIDDELDE','LEEFTIJD','MAN'],
        }


        for xls_url, reference_date in find_available_datasets(response, extension='xls').iteritems():
            reference_year = reference_date.year # different years in document
            reference_date = str(reference_date)

            staff_per_school = {}

            sheets = parse_xls_sheets_from_url(xls_url)
            for row in islice(sheets['per bestuur-brin-vak-graad'], None):
                brin = row.pop('BRIN NUMMER', None).strip()

                course = row.pop('VAK', None).strip()
                degree = int(float(row.pop('GRAAD', None)))
                staff_per_year = {}

                # loop over all rows, filtering key names and extracting year
                for key, val in row.items():
                    key = key.split()
                    if key[-1].isdigit() and len(key[-1]) == 4:

                        year = int(key[-1])
                        if year not in staff_per_year:
                            staff_per_year[year] = {}

                        # missing values are starred, for privacy reasons
                        if val != '*':

                            for k,v in INTS.items():
                                if key[:-1] == v:
                                    staff_per_year[year][k] = int(float(val or 0))

                            for k,v in FLOATS.items():
                                if key[:-1] == v:
                                    staff_per_year[year][k] = float(val or 0) or None

                            if key[0:2] == ['AANTAL', 'PERSONEN'] and key[-2] == 'JAAR':
                                if 'staff_age' not in staff_per_year[year]:
                                    staff_per_year[year]['staff_age'] = []
                                key_range = '>%s' % key[-3] if key[2] == 'OUDER' \
                                       else '<%s' % key[-3] if key[2] == 'JONGER' \
                                       else ''.join(key[2:-2])
                                staff_per_year[year]['staff_age'].append({
                                    'age_range': key_range,
                                    'count': int(float(val or 0)),
                                })
                            if key[:-1] == ['LEEFTIJD', 'ONBEKEND']:
                                if 'staff_age' not in staff_per_year[year]:
                                    staff_per_year[year]['staff_age'] = []
                                staff_per_year[year]['staff_age'].append({
                                    'age_range': '?',
                                    'count': int(float(val or 0)),
                                })

                # add rows per function group
                for year, staff in staff_per_year.items():
                    if (year, brin) not in staff_per_school:
                        staff_per_school[(year, brin)] = []

                    staff_per_school[(year, brin)].append({
                        'course': course,
                        'degree': degree,
                        'staff': staff if staff['staff'] else {'staff': 0},
                    })


            for (year, brin), per_school in staff_per_school.iteritems():
                school = DuoVoSchool(
                    brin=brin,
                    reference_year=year,
                    staff_per_course_reference_url=xls_url,
                    staff_per_course_reference_date=reference_date,
                    staff_per_course=per_school
                )
                yield school

    def parse_vo_time_per_course(self, response):
        """
        Voortgezet onderwijs > Personeel
        Parse "04. Gegeven lesuren per vak"
        """
        INTS = {
            'time':
                ['AANTAL','LESUREN'],
            'time_perm':
                ['AANTAL','LESUREN','PERSONEEL','IN','VASTE','DIENST'],
            'time_temp':
                ['AANTAL','LESUREN','PERSONEEL','IN','TIJDELIJKE','DIENST'],
            'time_substitutes':
                ['AANTAL','LESUREN','PERSONEEL','IN','DIENST','ALS','VERVANGING'],
            'time_unappointed':
                ['AANTAL','LESUREN','PERSONEEL','IN','DIENST','ZONDER','BENOEMING'],
            'time_appointment_unknown':
                ['AANTAL','LESUREN','PERSONEEL','MET','ONBEKEND','DIENSTVERBAND'],
            'time_female':
                ['AANTAL','LESUREN','VROUWEN'],
            'time_male':
                ['AANTAL','LESUREN','MANNEN'],
            'time_gender_unknown':
                ['AANTAL','LESUREN','PERSONEEL','GESLACHT','ONBEKEND'],

        }
        # Already in parse_vo_staff_course
        FLOATS = {
        #     'age_mean':
        #         ['GEMIDDELDE','LEEFTIJD'],
        #     'age_mean_female':
        #         ['GEMIDDELDE','LEEFTIJD','VROUW'],
        #     'age_mean_male':
        #         ['GEMIDDELDE','LEEFTIJD','MAN'],
        }


        for xls_url, reference_date in find_available_datasets(response, extension='xls').iteritems():
            reference_year = reference_date.year # different years in document
            reference_date = str(reference_date)

            time_per_school = {}

            sheets = parse_xls_sheets_from_url(xls_url)
            for row in islice(sheets['per bestuur-brin-vak-graad'], None):
                brin = row.pop('BRIN NUMMER', None).strip()

                course = row.pop('VAK', None).strip()
                degree = int(float(row.pop('GRAAD', None)))
                time_per_year = {}

                # loop over all rows, filtering key names and extracting year
                for key, val in row.items():
                    key = key.split()
                    if key[-1].isdigit() and len(key[-1]) == 4:

                        year = int(key[-1])
                        if year not in time_per_year:
                            time_per_year[year] = {}

                        # missing values are starred, for privacy reasons
                        if val != '*':

                            for k,v in INTS.items():
                                if key[:-1] == v:
                                    time_per_year[year][k] = int(float(val or 0))

                            for k,v in FLOATS.items():
                                if key[:-1] == v:
                                    time_per_year[year][k] = float(val or 0) or None

                            if key[0:3] == ['AANTAL','LESUREN','PERSONEEL'] and key[-2] == 'JAAR':
                                if 'time_by_staff_age' not in time_per_year[year]:
                                    time_per_year[year]['time_by_staff_age'] = []
                                key_range = '>%s' % key[-3] if key[3] == 'OUDER' \
                                       else '<%s' % key[-3] if key[3] == 'JONGER' \
                                       else ''.join(key[3:-2])
                                time_per_year[year]['time_by_staff_age'].append({
                                    'staff_age': key_range,
                                    'time': int(float(val or 0)),
                                })
                            if key[:-1] == ['AANTAL','LESUREN','PERSONEEL','LEEFTIJD', 'ONBEKEND']:
                                if 'time_by_staff_age' not in time_per_year[year]:
                                    time_per_year[year]['time_by_staff_age'] = []
                                time_per_year[year]['time_by_staff_age'].append({
                                    'staff_age': '?',
                                    'time': int(float(val or 0)),
                                })

                # add rows per function group
                for year, time in time_per_year.items():
                    if (year, brin) not in time_per_school:
                        time_per_school[(year, brin)] = []

                    # only add this course if it has been taught this year
                    if time['time']:
                        time_per_school[(year, brin)].append({
                            'course': course,
                            'degree': degree,
                            'time': time,
                        })


            for (year, brin), per_school in time_per_school.iteritems():
                school = DuoVoSchool(
                    brin=brin,
                    reference_year=year,
                    time_per_course_reference_url=xls_url,
                    time_per_course_reference_date=reference_date,
                    time_per_course=per_school
                )
                yield school

class DuoVoBranchesSpider(DuoSpider):
    name = 'duo_vo_branches'

    def __init__(self, *args, **kwargs):
        self.make_item = lambda ((brin,branch_id)): DuoVoBranch(brin=brin,branch_id=branch_id)
        self.requests = {
            'vo/adressen/Adressen/vestigingen.asp':
                self.parse_branches,
            'vo/leerlingen/Leerlingen/vo_leerlingen2.asp':
                self.parse_student_residences,
            'vo/leerlingen/Leerlingen/vo_leerlingen1.asp':
                 self.parse_students_by_structure,
            'vo/leerlingen/Leerlingen/vo_leerlingen6.asp':
                self.student_graduations,
            'vo/leerlingen/Leerlingen/vo_leerlingen7.asp':
                self.student_exam_grades,
            'vo/leerlingen/Leerlingen/vo_leerlingen8.asp':
                self.vmbo_exam_grades_per_course,
            'vo/leerlingen/Leerlingen/vo_leerlingen9.asp':
                self.havo_exam_grades_per_course,
            'vo/leerlingen/Leerlingen/vo_leerlingen10.asp':
                self.vwo_exam_grades_per_course,
            'vo/leerlingen/Leerlingen/vo_leerlingen3.asp':
                self.parse_vavo_students,
            'vo/leerlingen/Leerlingen/vo_leerlingen5.asp':
                self.parse_students_by_finegrained_structure,
            'Stroom/doorstromers/doorstromers/po_vo.asp':
                self.parse_vo_student_flow,
        }
        DuoSpider.__init__(self, *args, **kwargs)

    def parse_branches(self, response):
        """
        Parse "02. Adressen alle vestigingen"
        """

        for csv_url, reference_date in find_available_datasets(response).iteritems():
            reference_year = reference_date.year
            reference_date = str(reference_date)
            for row in parse_csv_file(csv_url):
                school = DuoVoBranch()

                school['reference_year'] = reference_year
                school['ignore_id_fields'] = ['reference_year']
                school['name'] = row['VESTIGINGSNAAM'].strip()
                school['address'] = {
                    'street': '%s %s' % (row['STRAATNAAM'].strip(),
                                         row['HUISNUMMER-TOEVOEGING'].strip()),
                    'city': row['PLAATSNAAM'].strip(),
                    'zip_code': row['POSTCODE'].strip().replace(' ', '')
                }

                school['website'] = row['INTERNETADRES'].strip() or None

                school['denomination'] = row['DENOMINATIE'].strip() or None

                if row['ONDERWIJSSTRUCTUUR'].strip():
                    school['education_structures'] = row['ONDERWIJSSTRUCTUUR']\
                        .strip().split('/')
                else:
                    school['education_structures'] = None

                school['province'] = row['PROVINCIE'].strip() or None

                school['board_id'] = int_or_none(row['BEVOEGD GEZAG NUMMER'].strip())

                if row['BRIN NUMMER'].strip():
                    school['brin'] = row['BRIN NUMMER'].strip()

                if row['VESTIGINGSNUMMER'].strip():
                    school['branch_id'] = int(row['VESTIGINGSNUMMER']
                                              .strip()
                                              .replace(row['BRIN NUMMER'], ''))

                school['municipality'] = row['GEMEENTENAAM'].strip() or None

                school['municipality_code'] = int_or_none(row['GEMEENTENUMMER'].strip())

                school['phone'] = row['TELEFOONNUMMER'].strip() or None

                school['correspondence_address'] = {}
                if row['STRAATNAAM CORRESPONDENTIEADRES'].strip():
                    school['correspondence_address']['street'] = '%s %s'\
                        % (row['STRAATNAAM CORRESPONDENTIEADRES'].strip(),
                           row['HUISNUMMER-TOEVOEGING CORRESPONDENTIEADRES'].strip())
                else:
                    school['correspondence_address']['street'] = None

                if row['POSTCODE CORRESPONDENTIEADRES'].strip():
                    school['correspondence_address']['zip_code'] = row[
                        'POSTCODE CORRESPONDENTIEADRES'].strip().replace(' ', '')
                else:
                    school['correspondence_address']['zip_code'] = None

                school['correspondence_address']['city'] = row[
                        'PLAATSNAAM CORRESPONDENTIEADRES'].strip() or None

                school['nodal_area'] = row['NODAAL GEBIED NAAM'].strip() or None

                school['nodal_area_code'] = int_or_none(row['NODAAL GEBIED CODE']
                                                    .strip())

                school['rpa_area'] = row['RPA-GEBIED NAAM'].strip() or None

                school['rpa_area_code'] = int_or_none(row['RPA-GEBIED CODE'].strip())

                school['wgr_area'] = row['WGR-GEBIED NAAM'].strip() or None

                school['wgr_area_code'] = int_or_none(row['WGR-GEBIED CODE'].strip())

                school['corop_area'] = row['COROPGEBIED NAAM'].strip() or None

                school['corop_area_code'] = int_or_none(row['COROPGEBIED CODE'].strip())

                school['education_area'] = row['ONDERWIJSGEBIED NAAM'].strip() or None

                if row['ONDERWIJSGEBIED CODE'].strip():
                    school['education_area_code'] = int(row['ONDERWIJSGEBIED '
                                                            'CODE'].strip())
                else:
                    school['education_area_code'] = None

                school['rmc_region'] = row['RMC-REGIO NAAM'].strip() or None

                school['rmc_region_code'] = int_or_none(row['RMC-REGIO CODE'].strip())

                yield school

    def parse_students_by_structure(self, response):
        """
        Parse "01. Leerlingen per vestiging naar onderwijstype, lwoo
        indicatie, sector, afdeling, opleiding"
        """

        def parse_row(row):
            brin = row['BRIN NUMMER'].strip()
            branch_id =  int((row['VESTIGINGSNUMMER'] or '0')
                             .strip().replace(row['BRIN NUMMER'], ''))

            education_type = {}

            department = row['AFDELING'].strip()
            if department:
                education_type['department'] = department
                if education_type['department'].lower() == 'n.v.t.':
                    education_type['department'] = None
            else:
                education_type['department'] = None

            if row['ELEMENTCODE'].strip():
                education_type['elementcode'] = int(row['ELEMENTCODE']
                                                    .strip())
            else:
                education_type['elementcode'] = None

            lwoo = row['LWOO INDICATIE'].strip().lower()
            if lwoo:
                if lwoo == 'j':
                    education_type['lwoo'] = True
                elif lwoo == 'n':
                    education_type['lwoo'] = False
                else:
                    education_type['lwoo'] = None
            else:
                education_type['lwoo'] = None

            vmbo_sector = row['VMBO SECTOR'].strip()
            if vmbo_sector:
                if vmbo_sector.lower() == 'n.v.t.':
                    education_type['vmbo_sector'] = None
                else:
                    education_type['vmbo_sector'] = vmbo_sector
            else:
                education_type['vmbo_sector'] = None

            naam = row['OPLEIDINGSNAAM'].strip()
            education_type['education_name'] = naam or None

            otype = row['ONDERWIJSTYPE VO EN LEER- OF VERBLIJFSJAAR'].strip()
            education_type['education_structure'] = otype or None

            for available_year in range(1, 7):
                male = int(row['LEER- OF VERBLIJFSJAAR %s - MAN'
                           % available_year])
                female = int(row['LEER- OF VERBLIJFSJAAR %s - VROUW'
                             % available_year])

                education_type['year_%s' % available_year] = {
                    'male': male,
                    'female': female,
                    'total': male + female
                }

            yield (brin, branch_id), education_type

        return self.dataset(response, self.make_item, 'students_by_structure', parse_row)


    def parse_student_residences(self, response):
        """
        Parse "02. Leerlingen per vestiging naar postcode leerling en
        leerjaar"
        """

        def parse_row(row):
            brin = row['BRIN NUMMER'].strip()
            branch_id =  int(row['VESTIGINGSNUMMER']
                             .strip().replace(row['BRIN NUMMER'], ''))

            yield (brin, branch_id), {
                'zip_code': row['POSTCODE LEERLING'].strip(),
                'city': row['PLAATSNAAM LEERLING'].strip().capitalize(),
                'municipality': row['GEMEENTENAAM LEERLING'].strip(),
                'municipality_id': int(row['GEMEENTENUMMER LEERLING'].strip()),
                'year_1': int(row['LEER- OF VERBLIJFSJAAR 1']),
                'year_2': int(row['LEER- OF VERBLIJFSJAAR 2']),
                'year_3': int(row['LEER- OF VERBLIJFSJAAR 3']),
                'year_4': int(row['LEER- OF VERBLIJFSJAAR 4']),
                'year_5': int(row['LEER- OF VERBLIJFSJAAR 5']),
                'year_6': int(row['LEER- OF VERBLIJFSJAAR 6'])
            }

        return self.dataset(response, self.make_item, 'student_residences', parse_row)


    def student_graduations(self, response):
        """
        Parse "06. Examenkandidaten en geslaagden"
        """

        for csv_url, reference_date in find_available_datasets(response).iteritems():
            reference_year = reference_date.year
            reference_date = str(reference_date)
            school_ids = {}
            graduations_school_year = {}
            for row in parse_csv_file(csv_url):
                # Remove newline chars and strip leading and trailing
                # whitespace.
                for key in row.keys():
                    row[key.replace('\n', '')] = row[key].strip()
                    del row[key]
                if 'BRIN NUMMER' in row:
                    brin = row['BRIN NUMMER']
                    branch_id = int(row['VESTIGINGSNUMMER'].replace(brin, ''))
                    school_id = '%s-%s' % (brin, branch_id)

                    school_ids[school_id] = {
                        'brin': brin,
                        'branch_id': branch_id
                    }

                    if school_id not in graduations_school_year:
                        graduations_school_year[school_id] = {}

                    # The years present in the CVS file (keys) and the
                    # normalized form we will use (values)
                    years = {
                        'SCHOOLJAAR 2007-2008': '2007-2008',
                        'SCHOOLJAAR 2008-2009': '2008-2009',
                        'SCHOOLJAAR 2009-2010': '2009-2010',
                        'SCHOOLJAAR 2010-2011': '2010-2011',
                        'SCHOOLJAAR 2011-2012': '2011-2012',
                    }

                    # Available breakdowns and their normalized form
                    breakdown = {
                        'MAN': 'male',
                        'VROUW': 'female',
                        'ONBEKEND': 'unknown'
                    }

                    for year, y_normal in years.iteritems():
                        if y_normal not in graduations_school_year[school_id]:
                            # Graduation info of a single school year
                            graduations_school_year[school_id][y_normal] = {
                                'year': y_normal,
                                'failed': 0,
                                'passed': 0,
                                'candidates': 0,
                                'per_department': []
                            }

                        # Total number of candidates (not always present for
                        # every year)
                        try:
                            candidates = row['EXAMENKANDIDATEN %s TOTAAL' % year]
                            if candidates:
                                candidates = int(candidates)
                            else:
                                continue
                        except KeyError:
                            candidates = 0
                        graduations_school_year[school_id][y_normal]['candidates'] += candidates

                        # Total number of successful graduations
                        try:
                            passed = int(row['GESLAAGDEN %s TOTAAL' % year])
                        except KeyError:
                            passed = 0
                        graduations_school_year[school_id][y_normal]['passed'] += passed

                        graduations_school_year[school_id][y_normal]['failed'] += (candidates - passed)

                        # Graduations for a singel department, by gender
                        department = {
                            'education_structure': row['ONDERWIJSTYPE VO'],
                            'department': row['OPLEIDINGSNAAM'],
                            'inspectioncode': row['INSPECTIECODE'],
                            'passed': {},
                            'failed': {},
                            'candidates': {},
                        }

                        for gender, gender_normal in breakdown.iteritems():
                            try:
                                candidates = row['EXAMENKANDIDATEN %s %s' % (year,
                                                 gender)]
                            except KeyError:
                                continue

                            # Skip gender if no value
                            if not candidates:
                                continue

                            candidates = int(candidates)
                            department['candidates'][gender_normal] = candidates

                            try:
                                passed = int(row['GESLAAGDEN %s %s' % (year, gender)])
                            except KeyError:
                                continue
                            department['passed'][gender_normal] = passed

                            failed = candidates - passed
                            department['failed'][gender_normal] = failed

                        graduations_school_year[school_id][y_normal]['per_department'].append(department)

            for school_id, graduations in graduations_school_year.iteritems():
                school = DuoVoBranch(
                    brin=school_ids[school_id]['brin'],
                    branch_id=school_ids[school_id]['branch_id'],
                    reference_year=reference_year,
                    graduations_reference_url=csv_url,
                    graduations_reference_date=reference_date,
                    graduations=[graduations[year] for year in graduations]
                )

                yield school

    def student_exam_grades(self, response):
        """
        Parse "07. Geslaagden, gezakten en gemiddelde examencijfers per instelling"
        """

        def parse_row(row):
            # Remove newline chars and strip leading and trailing
            # whitespace.
            for key in row.keys():
                c_key = key.replace('\n', '')
                row[c_key] = row[key].strip()

                if not row[c_key]:
                    del row[c_key]

            brin = row['BRIN NUMMER']
            branch_id = int(row['VESTIGINGSNUMMER'].replace(brin, ''))

            grades = {
                'education_structure': row['ONDERWIJSTYPE VO']
            }

            if 'LEERWEG VMBO' in row:
                grades['education_structure'] += '-%s' % row['LEERWEG VMBO']

            if 'VMBO SECTOR' in row:
                grades['vmbo_sector'] = row['VMBO SECTOR']

            if 'AFDELING' in row:
                grades['sector'] = row['AFDELING']

            if 'EXAMENKANDIDATEN' in row:
                grades['candidates'] = int(row['EXAMENKANDIDATEN'])

            if 'GESLAAGDEN' in row:
                grades['passed'] = int(row['GESLAAGDEN'])

            if 'GEZAKTEN' in row:
                grades['failed'] = int(row['GEZAKTEN'])

            if 'GEMIDDELD CIJFER SCHOOLEXAMEN' in row:
                grades['avg_grade_school_exam'] = float(row[
                    'GEMIDDELD CIJFER SCHOOLEXAMEN'].replace(',', '.'))

            if 'GEMIDDELD CIJFER CENTRAAL EXAMEN' in row:
                grades['avg_grade_central_exam'] = float(row[
                    'GEMIDDELD CIJFER CENTRAAL EXAMEN'].replace(',', '.'))

            if 'GEMIDDELD CIJFER CIJFERLIJST' in row:
                grades['avg_final_grade'] = float(row[
                    'GEMIDDELD CIJFER CIJFERLIJST'].replace(',', '.'))

            yield (brin, branch_id), grades

        return self.dataset(response, self.make_item, 'exam_grades', parse_row)

    def vmbo_exam_grades_per_course(self, response):
        """
        Parse "08. Examenkandidaten vmbo en examencijfers per vak per instelling"
        """

        def parse_row(row):
            # Remove newline chars and strip leading and trailing
            # whitespace.
            for key in row.keys():
                c_key = key.replace('\n', '')
                row[c_key] = row[key].strip()

                if not row[c_key]:
                    del row[c_key]

            brin = row['BRIN NUMMER']
            branch_id = int(row['VESTIGINGSNUMMER'])

            grades = {
                'education_structure': '%s-%s' % (row['ONDERWIJSTYPE VO'],
                                                  row['LEERWEG']),
                'course_identifier': row['VAKCODE'],
                'course_abbreviation': row['AFKORTING VAKNAAM'],
                'course_name': row['VAKNAAM']

            }

            if 'SCHOOLEXAMEN BEOORDELING' in row:
                grades['school_exam_rating'] = row['SCHOOLEXAMEN BEOORDELING']

            if 'TOTAAL AANTAL SCHOOLEXAMENS MET BEOORDELING' in row:
                grades['amount_of_school_exams_with_rating'] = int(row[
                    'TOTAAL AANTAL SCHOOLEXAMENS MET BEOORDELING'])

            if 'AANTAL SCHOOLEXAMENS MET BEOORDELING MEETELLEND VOOR DIPLOMA' in row:
                grades['amount_of_school_exams_with_rating_counting_'
                       'for_diploma'] = int(row['AANTAL SCHOOLEXAMENS MET '
                                                'BEOORDELING MEETELLEND VOOR '
                                                'DIPLOMA'])

            if 'TOTAAL AANTAL SCHOOLEXAMENS MET CIJFER' in row:
                grades['amount_of_school_exams_with_grades'] = int(row[
                    'TOTAAL AANTAL SCHOOLEXAMENS MET CIJFER'])

            if 'GEM. CIJFER TOTAAL AANTAL SCHOOLEXAMENS' in row:
                grades['avg_grade_school_exams'] = float(row[
                    'GEM. CIJFER TOTAAL AANTAL SCHOOLEXAMENS']
                    .replace(',', '.'))

            if 'AANTAL SCHOOLEXAMENS MET CIJFER MEETELLEND VOOR DIPLOMA' in row:
                grades['amount_of_school_exams_with_grades_counting_'
                       'for_diploma'] = int(row['AANTAL SCHOOLEXAMENS MET '
                                                'CIJFER MEETELLEND VOOR DIPLOMA'])

            if 'GEM. CIJFER SCHOOLEXAMENS MET CIJFER MEETELLEND VOOR DIPLOMA' in row:
                grades['avg_grade_school_exams_counting_for_diploma'] = \
                    float(row['GEM. CIJFER SCHOOLEXAMENS MET CIJFER '
                              'MEETELLEND VOOR DIPLOMA'].replace(',', '.'))

            if 'TOTAAL AANTAL CENTRALE EXAMENS' in row:
                grades['amount_of_central_exams'] = int(row['TOTAAL AANTAL'
                                                            ' CENTRALE EXAMENS'])

            if 'GEM. CIJFER TOTAAL AANTAL CENTRALE EXAMENS' in row:
                grades['avg_grade_central_exams'] = float(row[
                    'GEM. CIJFER TOTAAL AANTAL CENTRALE EXAMENS']
                    .replace(',', '.'))

            if 'AANTAL CENTRALE EXAMENS MEETELLEND VOOR DIPLOMA' in row:
                grades['amount_of_central_exams_counting_for_diploma'] = \
                    int(row['AANTAL CENTRALE EXAMENS MEETELLEND VOOR DIPLOMA'])

            if 'GEM. CIJFER CENTRALE EXAMENS MET CIJFER MEETELLEND VOOR DIPLOMA' in row:
                grades['avg_grade_central_exams_counting_for_diploma'] = \
                    float(row['GEM. CIJFER CENTRALE EXAMENS MET CIJFER '
                              'MEETELLEND VOOR DIPLOMA'].replace(',', '.'))

            if 'GEM. CIJFER CIJFERLIJST' in row:
                grades['average_grade_overall'] = float(row[
                    'GEM. CIJFER CIJFERLIJST'].replace(',', '.'))

            yield (brin, branch_id), grades

        return self.dataset(response, self.make_item, 'vmbo_exam_grades_per_course', parse_row)

    def havo_exam_grades_per_course(self, response):
        """
        Parse "09. Examenkandidaten havo en examencijfers per vak per instelling"
        """

        def parse_row(row):

            # Remove newline chars and strip leading and trailing
            # whitespace.
            for key in row.keys():
                c_key = key.replace('\n', '')
                row[c_key] = row[key].strip()

                if not row[c_key]:
                    del row[c_key]

            brin = row['BRIN NUMMER']
            branch_id = int(row['VESTIGINGSNUMMER'])

            grades = {
                'education_structure': row['ONDERWIJSTYPE VO'],
                'course_identifier': row['VAKCODE'],
                'course_abbreviation': row['AFKORTING VAKNAAM'],
                'course_name': row['VAKNAAM']

            }

            if 'SCHOOLEXAMEN BEOORDELING' in row:
                grades['school_exam_rating'] = row['SCHOOLEXAMEN BEOORDELING']

            if 'TOTAAL AANTAL SCHOOLEXAMENS MET BEOORDELING' in row:
                grades['amount_of_school_exams_with_rating'] = int(row[
                    'TOTAAL AANTAL SCHOOLEXAMENS MET BEOORDELING'])

            if 'AANTAL SCHOOLEXAMENS MET BEOORDELING MEETELLEND VOOR DIPLOMA' in row:
                grades['amount_of_school_exams_with_rating_counting_'
                       'for_diploma'] = int(row['AANTAL SCHOOLEXAMENS MET '
                                                'BEOORDELING MEETELLEND VOOR '
                                                'DIPLOMA'])

            if 'TOTAAL AANTAL SCHOOLEXAMENS MET CIJFER' in row:
                grades['amount_of_school_exams_with_grades'] = int(row[
                    'TOTAAL AANTAL SCHOOLEXAMENS MET CIJFER'])

            if 'GEM. CIJFER TOTAAL AANTAL SCHOOLEXAMENS' in row:
                grades['avg_grade_school_exams'] = float(row[
                    'GEM. CIJFER TOTAAL AANTAL SCHOOLEXAMENS']
                    .replace(',', '.'))

            if 'AANTAL SCHOOLEXAMENS MET CIJFER MEETELLEND VOOR DIPLOMA' in row:
                grades['amount_of_school_exams_with_grades_counting_'
                       'for_diploma'] = int(row['AANTAL SCHOOLEXAMENS MET '
                                                'CIJFER MEETELLEND VOOR DIPLOMA'])

            if 'GEM. CIJFER SCHOOLEXAMENS MET CIJFER MEETELLEND VOOR DIPLOMA' in row:
                grades['avg_grade_school_exams_counting_for_diploma'] = \
                    float(row['GEM. CIJFER SCHOOLEXAMENS MET CIJFER '
                              'MEETELLEND VOOR DIPLOMA'].replace(',', '.'))

            if 'TOTAAL AANTAL CENTRALE EXAMENS' in row:
                grades['amount_of_central_exams'] = int(row['TOTAAL AANTAL'
                                                            ' CENTRALE EXAMENS'])

            if 'GEM. CIJFER TOTAAL AANTAL CENTRALE EXAMENS' in row:
                grades['avg_grade_central_exams'] = float(row[
                    'GEM. CIJFER TOTAAL AANTAL CENTRALE EXAMENS']
                    .replace(',', '.'))

            if 'AANTAL CENTRALE EXAMENS MEETELLEND VOOR DIPLOMA' in row:
                grades['amount_of_central_exams_counting_for_diploma'] = \
                    int(row['AANTAL CENTRALE EXAMENS MEETELLEND VOOR DIPLOMA'])

            if 'GEM. CIJFER CENTRALE EXAMENS MET CIJFER MEETELLEND VOOR DIPLOMA' in row:
                grades['avg_grade_central_exams_counting_for_diploma'] = \
                    float(row['GEM. CIJFER CENTRALE EXAMENS MET CIJFER '
                              'MEETELLEND VOOR DIPLOMA'].replace(',', '.'))

            if 'GEM. CIJFER CIJFERLIJST' in row:
                grades['average_grade_overall'] = float(row[
                    'GEM. CIJFER CIJFERLIJST'].replace(',', '.'))

            yield (brin, branch_id), grades

        return self.dataset(response, self.make_item, 'havo_exam_grades_per_course', parse_row)

    def vwo_exam_grades_per_course(self, response):
        """
        Parse "10. Examenkandidaten vwo en examencijfers per vak per instelling"
        """

        def parse_row(row):
            # Remove newline chars and strip leading and trailing
            # whitespace.
            for key in row.keys():
                c_key = key.replace('\n', '')
                row[c_key] = row[key].strip()

                if not row[c_key]:
                    del row[c_key]

            brin = row['BRIN NUMMER']
            branch_id = int(row['VESTIGINGSNUMMER'])

            grades = {
                'education_structure': row['ONDERWIJSTYPE VO'],
                'course_identifier': row['VAKCODE'],
                'course_abbreviation': row['AFKORTING VAKNAAM'],
                'course_name': row['VAKNAAM']
            }

            if 'SCHOOLEXAMEN BEOORDELING' in row:
                grades['school_exam_rating'] = row['SCHOOLEXAMEN BEOORDELING']

            if 'TOTAAL AANTAL SCHOOLEXAMENS MET BEOORDELING' in row:
                grades['amount_of_school_exams_with_rating'] = int(row[
                    'TOTAAL AANTAL SCHOOLEXAMENS MET BEOORDELING'])

            if 'AANTAL SCHOOLEXAMENS MET BEOORDELING MEETELLEND VOOR DIPLOMA' in row:
                grades['amount_of_school_exams_with_rating_counting_'
                       'for_diploma'] = int(row['AANTAL SCHOOLEXAMENS MET '
                                                'BEOORDELING MEETELLEND VOOR '
                                                'DIPLOMA'])

            if 'TOTAAL AANTAL SCHOOLEXAMENS MET CIJFER' in row:
                grades['amount_of_school_exams_with_grades'] = int(row[
                    'TOTAAL AANTAL SCHOOLEXAMENS MET CIJFER'])

            if 'GEM. CIJFER TOTAAL AANTAL SCHOOLEXAMENS' in row:
                grades['avg_grade_school_exams'] = float(row[
                    'GEM. CIJFER TOTAAL AANTAL SCHOOLEXAMENS']
                    .replace(',', '.'))

            if 'AANTAL SCHOOLEXAMENS MET CIJFER MEETELLEND VOOR DIPLOMA' in row:
                grades['amount_of_school_exams_with_grades_counting_'
                       'for_diploma'] = int(row['AANTAL SCHOOLEXAMENS MET '
                                                'CIJFER MEETELLEND VOOR DIPLOMA'])

            if 'GEM. CIJFER SCHOOLEXAMENS MET CIJFER MEETELLEND VOOR DIPLOMA' in row:
                grades['avg_grade_school_exams_counting_for_diploma'] = \
                    float(row['GEM. CIJFER SCHOOLEXAMENS MET CIJFER '
                              'MEETELLEND VOOR DIPLOMA'].replace(',', '.'))

            if 'TOTAAL AANTAL CENTRALE EXAMENS' in row:
                grades['amount_of_central_exams'] = int(row['TOTAAL AANTAL'
                                                            ' CENTRALE EXAMENS'])

            if 'GEM. CIJFER TOTAAL AANTAL CENTRALE EXAMENS' in row:
                grades['avg_grade_central_exams'] = float(row[
                    'GEM. CIJFER TOTAAL AANTAL CENTRALE EXAMENS']
                    .replace(',', '.'))

            if 'AANTAL CENTRALE EXAMENS MEETELLEND VOOR DIPLOMA' in row:
                grades['amount_of_central_exams_counting_for_diploma'] = \
                    int(row['AANTAL CENTRALE EXAMENS MEETELLEND VOOR DIPLOMA'])

            if 'GEM. CIJFER CENTRALE EXAMENS MET CIJFER MEETELLEND VOOR DIPLOMA' in row:
                grades['avg_grade_central_exams_counting_for_diploma'] = \
                    float(row['GEM. CIJFER CENTRALE EXAMENS MET CIJFER '
                              'MEETELLEND VOOR DIPLOMA'].replace(',', '.'))

            if 'GEM. CIJFER CIJFERLIJST' in row:
                grades['average_grade_overall'] = float(row[
                    'GEM. CIJFER CIJFERLIJST'].replace(',', '.'))

            yield (brin, branch_id), grades

        return self.dataset(response, self.make_item, 'vwo_exam_grades_per_course', parse_row)

    def parse_vavo_students(self, response):
        """
        Voortgezet onderwijs > Leerlingen
        Parse "Leerlingen per vestiging en bevoegd gezag (vavo apart)"
        """

        def parse_row(row):
            brin = row['BRIN NUMMER'].strip()
            branch_id = int(row['VESTIGINGSNUMMER'].strip()[-2:] or 0)

            vavo_students = {
                'non_vavo' : int(row['AANTAL LEERLINGEN'] or 0),
                'vavo' : int(row['AANTAL VO LEERLINGEN UITBESTEED AAN VAVO'] or 0),
            }
            yield (brin, branch_id), vavo_students

        return self.dataset(response, self.make_item, 'vavo_students', parse_row)

    def parse_students_by_finegrained_structure(self, response):
        """
        Voortgezet onderwijs > Leerlingen
        Parse "05. Leerlingen per samenwerkingsverband en onderwijstype"
        """

        def parse_row(row):
            brin = row['BRIN NUMMER'].strip()
            branch_id = int(row['VESTIGINGSNUMMER'].strip()[-2:] or 0)

            # Don't make special fiends, just use all of 'em
            fields = [
                'Brugjaar 1-2',
                'Engelse Stroom',
                'HAVO lj 4-5',
                'HAVO uitbest. aan VAVO',
                'HAVO/VWO lj 3',
                'Int. Baccelaureaat',
                'Praktijkonderwijs alle vj',
                'VMBO BL lj 3-4',
                'VMBO GL lj 3-4',
                'VMBO KL lj 3-4',
                'VMBO TL lj 3-4',
                'VMBO uitbest. aan VAVO',
                'VMBO-MBO2 lj 3-6',
                'VWO lj 4-6',
                'VWO uitbest. aan VAVO',
            ]
            for field, count in row.items():
                if field.strip() in fields and count:
                    yield (brin, branch_id), {'type': field.strip(), 'count': int(count)}

        return self.dataset(response, self.make_item, 'students_by_finegrained_structure', parse_row)

    def parse_vo_student_flow(self, response):
        """
        Stroominformatie > Doorstromers
        05. Doorstromers van primair naar voortgezet onderwijs
        """

        tables = []
        for csv_url, reference_date in find_available_datasets(response).iteritems():
            csv_file = requests.get(csv_url)
            csv_file.encoding = 'cp1252'
            csv_file = cStringIO.StringIO(csv_file.content
                  .decode('cp1252').encode('utf8'))
            tables.append( (csv_file, reference_date, csv_url) )

        # add local file
        if self.add_local_table is not None:
            try:
                reference_date = datetime.strptime(basename(self.add_local_table), '%Y-%m-%d.csv').date()
                tables.append( (file(self.add_local_table), reference_date, None) )
            except Exception as ex:
                print 'Could not add', self.add_local_table, ':',  ex

        for table, reference_date, csv_url in tables:
            reference_year = reference_date.year
            reference_date = str(reference_date)
            flow_per_branch = get_student_flow(table, for_po=False)

            for (brin, branch_id), per_school in flow_per_branch.iteritems():
                school = DuoVoBranch(
                    brin=brin,
                    branch_id=branch_id,
                    reference_year=reference_year,
                    student_flow_reference_url=csv_url,
                    student_flow_reference_date=reference_date,
                    student_flow=per_school
                )
                yield school



class DuoPoBoardsSpider(DuoSpider):
    name = 'duo_po_boards'

    def __init__(self, *args, **kwargs):
        self.make_item = lambda (board_id): DuoPoBoard(board_id=board_id)
        self.requests = {
            'po/adressen/Adressen/po_adressen05.asp':
                self.parse_po_boards,
            'po/Financien/Jaarrekeninggegevens/Kengetallen.asp':
                self.parse_po_financial_key_indicators,
            'po/Leerlingen/Leerlingen/po_leerlingen7.asp':
                self.parse_po_education_type,
            'po/Onderwijspersoneel/Personeel/po_personeel_personen.asp':
                self.parse_po_staff_people,
            'po/Onderwijspersoneel/Personeel/po_personeel_fte.asp':
                self.parse_po_staff_fte,
        }
        DuoSpider.__init__(self, *args, **kwargs)

    def parse_po_boards(self, response):
        """
        Primair onderwijs > Adressen
        Parse "05. Bevoegde gezagen basisonderwijs"
        """

        for csv_url, reference_date in find_available_datasets(response).iteritems():
            reference_year = reference_date.year
            reference_date = str(reference_date)
            for row in parse_csv_file(csv_url):
                # strip leading and trailing whitespace.
                for key in row.keys():
                    row[key] = row[key].strip()

                board = DuoPoBoard()
                board['board_id'] = int(row['BEVOEGD GEZAG NUMMER'])
                board['name'] = row['BEVOEGD GEZAG NAAM']
                board['address'] = {
                    'street': '%s %s' % (row['STRAATNAAM'],
                                         row['HUISNUMMER-TOEVOEGING']),
                    'zip_code': row['POSTCODE'].replace(' ', ''),
                    'city': row['PLAATSNAAM']
                }

                board['correspondence_address'] = {}
                if row['STRAATNAAM CORRESPONDENTIEADRES']:
                    board['correspondence_address']['street'] = '%s %s'\
                        % (row['STRAATNAAM CORRESPONDENTIEADRES'],
                           row['HUISNUMMER-TOEVOEGING CORRESPONDENTIEADRES'])
                else:
                    board['correspondence_address']['street'] = None

                if row['POSTCODE CORRESPONDENTIEADRES']:
                    board['correspondence_address']['zip_code'] = row[
                        'POSTCODE CORRESPONDENTIEADRES'].replace(' ', '')
                else:
                    board['correspondence_address']['zip_code'] = None

                board['correspondence_address']['city'] = row[
                        'PLAATSNAAM CORRESPONDENTIEADRES'] or None

                board['municipality'] = row['GEMEENTENAAM'] or None

                board['municipality_code'] = int_or_none(row['GEMEENTENUMMER'])

                board['phone'] = row['TELEFOONNUMMER'] or None

                board['website'] = row['INTERNETADRES'] or None

                board['denomination'] = row['DENOMINATIE'] or None

                board['administrative_office_id'] = \
                        int_or_none(row['ADMINISTRATIEKANTOORNUMMER'])

                board['reference_year'] = reference_year
                board['ignore_id_fields'] = ['reference_year']
                yield board

    def parse_po_financial_key_indicators(self, response):
        """
        Primair onderwijs > Financien > Jaarrekeninggegevens
        Parse "15. Kengetallen"
        """

        indicators_mapping = {
            'LIQUIDITEIT (CURRENT RATIO)': 'liquidity_current_ratio',
            'RENTABILITEIT': 'profitability',
            'SOLVABILITEIT 1': 'solvency_1',
            'SOLVABILITEIT 2': 'solvency_2',
            'ALGEMENE RESERVE / TOTALE BATEN': 'general_reserve_div_total_income',
            'BELEGGINGEN (T.O.V. EV)': 'investments_relative_to_equity',
            #'CONTRACTACTIVITEITEN / RIJKSBIJDRAGE': 'contract_activities_div_gov_funding',
            #'CONTRACTACTIVITEITEN / TOTALE BATEN': 'contractactivities_div_total_profits',
            'EIGEN VERMOGEN / TOTALE BATEN': 'equity_div_total_profits',
            #'INVESTERING HUISVESTING / TOTALE BATEN': 'housing_investment_div_total_profits',
            'INVESTERINGEN (INVENT.+APP.) / TOTALE BATEN': 'investments_div_total_profits',
            'KAPITALISATIEFACTOR': 'capitalization_ratio',
            'LIQUIDITEIT (QUICK RATIO)': 'liquidity_quick_ratio',
            'OV. OVERHEIDSBIJDRAGEN / TOT. BATEN': 'other_gov_funding_div_total_profits',
            'PERSONEEL / RIJKSBIJDRAGEN': 'staff_costs_div_gov_funding',
            'PERSONELE LASTEN / TOTALE LASTEN': 'staff_expenses_div_total_expenses',
            'RIJKSBIJDRAGEN / TOTALE BATEN': 'gov_funding_div_total_profits',
            'VOORZIENINGEN /TOTALE BATEN': 'facilities_div_total_profits',
            #'WEERSTANDSVERMOGEN (-/- MVA)': '',
            #'WEERSTANDSVERMOGEN VO TOTALE BN.': '',
            'WERKKAPITAAL / TOTALE BATEN': 'operating_capital_div_total_profits',
            'HUISVESTINGSLASTEN / TOTALE LASTEN': 'housing_expenses_div_total_expenses',
            'WERKKAPITAAL': 'operating_capital',
        }

        def parse_row(row):
            # Strip leading and trailing whitespace from field names and values.
            for key in row.keys():
                row[key.strip()] = row[key].strip()

            # Don't look at the 'Kengetallen per sector' file
            if 'BEVOEGD GEZAG NUMMER' in row:
                board_id = int(row['BEVOEGD GEZAG NUMMER'])

                indicators = {}
                indicators['year'] = int(row['JAAR'])
                indicators['group'] = row['GROEPERING']

                for ind, ind_norm in indicators_mapping.iteritems():
                    # Some fields have no value, just an empty string ''.
                    # Set those to 0.
                    if row[ind] == '':
                        row[ind] = '0'
                    indicators[ind_norm] = float(row[ind].replace('.', '')
                                                         .replace(',', '.'))

                yield board_id, indicators

        return self.dataset(response, self.make_item, 'financial_key_indicators_per_year', parse_row)


    def parse_po_education_type(self, response):
        """
        Primair onderwijs > Leerlingen
        Parse "07. Leerlingen primair onderwijs per bevoegd gezag naar denominatie en onderwijssoort"
        """

        possible_edu_types = ['BAO', 'SBAO', 'SO', 'VSO', '(V)SO']

        def parse_row(row):
            # Strip leading and trailing whitespace from field names and values.
            for key in row.keys():
                if row[key]:
                    row[key.strip()] = row[key].strip()
                else:
                    row[key.strip()] = '0'

            board_id = int(row['BEVOEGD GEZAG NUMMER'])

            for edu_type in possible_edu_types:
                if edu_type in row:
                    if row[edu_type] == '':
                        continue

                    if row[edu_type] == 0:
                        continue

                    yield board_id, {
                        'denomination': row['DENOMINATIE'],
                        'edu_type': edu_type,
                        'students': int(row[edu_type].replace('.', ''))
                    }

        return self.dataset(response, self.make_item, 'edu_types', parse_row)

    def parse_po_staff_people(self, response):
        """
        Primair onderwijs > Personeel
        Parse "01. Onderwijspersoneel in aantal personen"
        """

        for xls_url, reference_date in find_available_datasets(response, extension='xls').iteritems():
            reference_year = reference_date.year # different years in document
            reference_date = str(reference_date)
            staff_per_board = get_staff_people(xls_url, with_brin=False)

            for (year, board_id), per_board in staff_per_board.iteritems():
                board = DuoPoBoard(
                    board_id=board_id,
                    reference_year=year,
                    staff_reference_url=xls_url,
                    staff_reference_date=reference_date,
                    staff=per_board
                )
                yield board

    def parse_po_staff_fte(self, response):
        """
        Primair onderwijs > Personeel
        Parse "02. Onderwijspersoneel in aantal fte"
        """

        for xls_url, reference_date in find_available_datasets(response, extension='xls').iteritems():
            reference_year = reference_date.year # different years in document
            reference_date = str(reference_date)
            staff_per_board = get_staff_fte(xls_url, with_brin=False)

            for (year, board_id), per_board in staff_per_board.iteritems():
                board = DuoPoBoard(
                    board_id=board_id,
                    reference_year=year,
                    fte_reference_url=xls_url,
                    fte_reference_date=reference_date,
                    fte=per_board
                )
                yield board

class DuoPoSchoolsSpider(DuoSpider):
    name = 'duo_po_schools'

    def __init__(self, *args, **kwargs):
        self.make_item = lambda (brin): DuoPoSchool(brin=brin)
        self.requests = {
            'po/adressen/Adressen/hoofdvestigingen.asp':
                self.parse_po_schools,
            'po/Leerlingen/Leerlingen/po_leerlingen4.asp':
                self.parse_spo_students_per_cluster,
            'passendow/Adressen/Adressen/passend_po_2.asp':
                self.parse_po_lo_collaboration,
            'passendow/Adressen/Adressen/passend_po_4.asp':
                self.parse_pao_collaboration,
            'po/Onderwijspersoneel/Personeel/po_personeel_personen.asp':
                self.parse_po_staff_people,
            'po/Onderwijspersoneel/Personeel/po_personeel_fte.asp':
                self.parse_po_staff_fte,
        }
        DuoSpider.__init__(self, *args, **kwargs)

    def parse_po_schools(self, response):
        """
        Primair onderwijs > Adressen
        Parse: "01. Hoofdvestigingen basisonderwijs"
        """

        # Fields that do not need additonal processing
        school_fields = {
            'BRIN NUMMER': 'brin',
            'PROVINCIE': 'province',
            'INSTELLINGSNAAM': 'name',
            'GEMEENTENAAM': 'municipality',
            'DENOMINATIE': 'denomination',
            'INTERNETADRES': 'website',
            'TELEFOONNUMMER': 'phone',
            'ONDERWIJSGEBIED NAAM': 'education_area',
            'NODAAL GEBIED NAAM': 'nodal_area',
            'RPA-GEBIED NAAM': 'rpa_area',
            'WGR-GEBIED NAAM': 'wgr_area',
            'COROPGEBIED NAAM': 'corop_area',
            'RMC-REGIO NAAM': 'rmc_region'
        }

        for csv_url, reference_date in find_available_datasets(response).iteritems():
            reference_year = reference_date.year
            reference_date = str(reference_date)
            for row in parse_csv_file(csv_url):
                # strip leading and trailing whitespace.
                for key in row.keys():
                    value = row[key].strip()
                    row[key] = value or None

                school = DuoPoSchool()
                school['board_id'] = int(row['BEVOEGD GEZAG NUMMER'])
                school['address'] = {
                    'street': '%s %s' % (row['STRAATNAAM'],
                                         row['HUISNUMMER-TOEVOEGING']),
                    'city': row['PLAATSNAAM'],
                    'zip_code': row['POSTCODE'].replace(' ', '')
                }

                school['correspondence_address'] = {
                    'street': '%s %s' % (row['STRAATNAAM CORRESPONDENTIEADRES'],
                                         row['HUISNUMMER-TOEVOEGING '
                                             'CORRESPONDENTIEADRES']),
                    'city': row['PLAATSNAAM CORRESPONDENTIEADRES'],
                    'zip_code': row['POSTCODE CORRESPONDENTIEADRES']
                }

                school['municipality_code'] = int(row['GEMEENTENUMMER'])

                if row['COROPGEBIED CODE']:
                    school['corop_area_code'] = int(row['COROPGEBIED CODE'])

                school['nodal_area_code'] = int_or_none(row['NODAAL GEBIED CODE'])

                school['rpa_area_code'] = int_or_none(row['RPA-GEBIED CODE'])

                school['wgr_area_code'] = int_or_none(row['WGR-GEBIED CODE'])

                school['education_area_code'] = int_or_none(row['ONDERWIJSGEBIED CODE'])

                school['rmc_region_code'] = int_or_none(row['RMC-REGIO CODE'])

                for field, field_norm in school_fields.iteritems():
                    school[field_norm] = row[field]

                school['reference_year'] = reference_year
                school['ignore_id_fields'] = ['reference_year']

                yield school

    def parse_spo_students_per_cluster(self, response):
        """
        Primair onderwijs > Leerlingen
        Parse "04. Leerlingen speciaal onderwijs naar cluster"
        """

        def parse_row(row):
            if 'BRIN NUMMER' in row:
                yield row['BRIN NUMMER'], {
                    'cluster_1': int(row['CLUSTER 1']),
                    'cluster_2': int(row['CLUSTER 2']),
                    'cluster_3': int(row['CLUSTER 3']),
                    'cluster_4': int(row['CLUSTER 4']),
                }
        return self.dataset(response, self.make_item, 'spo_students_per_cluster', parse_row)

    def parse_po_lo_collaboration(self, response):
        """
        Passend onderwijs > Adressen
        Parse "02. Adressen instellingen per samenwerkingsverband lichte ondersteuning, primair onderwijs"
        """

        def parse_row(row):
            # strip leading and trailing whitespace.
            for key in row.keys():
                value = (row[key] or '').strip()
                row[key] = value or None
                row[key.strip()] = value or None

            if row.has_key('BRINNUMMER'):
                row['BRIN NUMMER'] = row['BRINNUMMER']

            brin = row['BRIN NUMMER']
            collaboration = row['ADMINISTRATIENUMMER']

            yield brin, collaboration

        return self.dataset(response, self.make_item, 'po_lo_collaboration', parse_row)

    def parse_pao_collaboration(self, response):
        """
        Passend onderwijs > Adressen
        Parse "04. Adressen instellingen per samenwerkingsverband passend onderwijs, primair onderwijs"
        """

        def parse_row(row):
            # strip leading and trailing whitespace.
            for key in row.keys():
                value = (row[key] or '').strip()
                row[key] = value or None
                row[key.strip()] = value or None

            if row.has_key('BRINNUMMER'):
                row['BRIN NUMMER'] = row['BRINNUMMER']

            brin = row['BRIN NUMMER']
            collaboration = row['ADMINISTRATIENUMMER']

            yield brin, collaboration

        return self.dataset(response, self.make_item, 'pao_collaboration', parse_row)

    def parse_po_staff_people(self, response):
        """
        Primair onderwijs > Personeel
        Parse "01. Onderwijspersoneel in aantal personen"
        """

        for xls_url, reference_date in find_available_datasets(response, extension='xls').iteritems():
            reference_year = reference_date.year # different years in document
            reference_date = str(reference_date)
            staff_per_school = get_staff_people(xls_url, with_brin=True)

            for (year, brin), per_school in staff_per_school.iteritems():
                school = DuoPoSchool(
                    brin=brin,
                    reference_year=year,
                    staff_reference_url=xls_url,
                    staff_reference_date=reference_date,
                    staff=per_school
                )
                yield school

    def parse_po_staff_fte(self, response):
        """
        Primair onderwijs > Personeel
        Parse "02. Onderwijspersoneel in aantal fte"
        """

        for xls_url, reference_date in find_available_datasets(response, extension='xls').iteritems():
            reference_year = reference_date.year # different years in document
            reference_date = str(reference_date)
            staff_per_school = get_staff_fte(xls_url, with_brin=True)

            for (year, brin), per_school in staff_per_school.iteritems():
                school = DuoPoSchool(
                    brin=brin,
                    reference_year=year,
                    fte_reference_url=xls_url,
                    fte_reference_date=reference_date,
                    fte=per_school
                )
                yield school

class DuoPoBranchesSpider(DuoSpider):
    name = 'duo_po_branches'

    def __init__(self, *args, **kwargs):
        self.make_item = lambda ((brin, board_id)): DuoPoBranch(brin=brin, board_id=board_id)
        self.requests = {
            'po/adressen/Adressen/vest_bo.asp':
                self.parse_po_branches,
            'po/Leerlingen/Leerlingen/po_leerlingen1.asp':
                self.parse_po_student_weight,
            'po/Leerlingen/Leerlingen/po_leerlingen3.asp':
                self.parse_po_student_age,
            'po/Leerlingen/Leerlingen/po_leerlingen9.asp':
                self.parse_po_born_outside_nl,
            'po/Leerlingen/Leerlingen/po_leerlingen11.asp':
                self.parse_po_pupil_zipcode_by_age,
            'po/Leerlingen/Leerlingen/leerjaar.asp':
                self.parse_po_student_year,
            'po/Leerlingen/Leerlingen/po_leerlingen5.asp':
                self.parse_spo_students_by_birthyear,
            'po/Leerlingen/Leerlingen/po_leerlingen6.asp':
                self.parse_spo_students_by_edu_type,
            'po/Leerlingen/Leerlingen/Schooladvies.asp':
                self.parse_po_students_by_advice,
            'po/Leerlingen/Leerlingen/po_leerlingen26-10.asp':
                self.parse_po_students_in_BRON,
            'Stroom/doorstromers/doorstromers/po_vo.asp':
                self.parse_po_student_flow,
        }
        DuoSpider.__init__(self, *args, **kwargs)

    def parse_po_branches(self, response):
        """
        Primair onderwijs > Adressen
        Parse "03. Alle vestigingen basisonderwijs"
        """

        for csv_url, reference_date in find_available_datasets(response).iteritems():
            reference_year = reference_date.year
            reference_date = str(reference_date)
            for row in parse_csv_file(csv_url):
                school = DuoPoBranch()

                # Correct this field name which has a trailing space.
                if row.has_key('VESTIGINGSNAAM '):
                    row['VESTIGINGSNAAM'] = row['VESTIGINGSNAAM ']

                school['reference_year'] = reference_year
                school['ignore_id_fields'] = ['reference_year']
                school['name'] = row['VESTIGINGSNAAM'].strip()
                school['address'] = {
                    'street': '%s %s' % (row['STRAATNAAM'].strip(),
                                         row['HUISNUMMER-TOEVOEGING'].strip()),
                    'city': row['PLAATSNAAM'].strip(),
                    'zip_code': row['POSTCODE'].strip().replace(' ', '')
                }

                school['website'] = row['INTERNETADRES'].strip() or None

                school['denomination'] = row['DENOMINATIE'].strip() or None

                school['province'] = row['PROVINCIE'].strip() or None

                school['board_id'] = int_or_none(row['BEVOEGD GEZAG NUMMER'].strip())

                if row['BRIN NUMMER'].strip():
                    school['brin'] = row['BRIN NUMMER'].strip()

                if row['VESTIGINGSNUMMER'].strip():
                    school['branch_id'] = int(row['VESTIGINGSNUMMER']
                                              .strip()
                                              .replace(row['BRIN NUMMER'], ''))

                school['municipality'] = row['GEMEENTENAAM'].strip() or None

                school['municipality_code'] = int_or_none(row['GEMEENTENUMMER'].strip())

                school['phone'] = row['TELEFOONNUMMER'].strip() or None

                school['correspondence_address'] = {}
                if row['STRAATNAAM CORRESPONDENTIEADRES'].strip():
                    school['correspondence_address']['street'] = '%s %s'\
                        % (row['STRAATNAAM CORRESPONDENTIEADRES'].strip(),
                           row['HUISNUMMER-TOEVOEGING CORRESPONDENTIEADRES'].strip())
                else:
                    school['correspondence_address']['street'] = None

                if row['POSTCODE CORRESPONDENTIEADRES'].strip():
                    school['correspondence_address']['zip_code'] = row[
                        'POSTCODE CORRESPONDENTIEADRES'].strip().replace(' ', '')
                else:
                    school['correspondence_address']['zip_code'] = None

                school['correspondence_address']['city'] = row[
                        'PLAATSNAAM CORRESPONDENTIEADRES'].strip() or None

                school['nodal_area'] = row['NODAAL GEBIED NAAM'].strip() or None

                if row['NODAAL GEBIED CODE'].strip():
                    school['nodal_area_code'] = int(row['NODAAL GEBIED CODE']
                                                    .strip())
                else:
                    school['nodal_area_code'] = None

                school['rpa_area'] = row['RPA-GEBIED NAAM'].strip() or None

                school['rpa_area_code'] = int_or_none(row['RPA-GEBIED CODE'].strip())

                school['wgr_area'] = row['WGR-GEBIED NAAM'].strip() or None

                school['wgr_area_code'] = int_or_none(row['WGR-GEBIED CODE'].strip())

                school['corop_area'] = row['COROPGEBIED NAAM'].strip() or None

                school['corop_area_code'] = int_or_none(row['COROPGEBIED CODE'].strip())

                school['education_area'] = row['ONDERWIJSGEBIED NAAM'].strip() or None

                if row['ONDERWIJSGEBIED CODE'].strip():
                    school['education_area_code'] = int(row['ONDERWIJSGEBIED '
                                                            'CODE'].strip())
                else:
                    school['education_area_code'] = None

                school['rmc_region'] = row['RMC-REGIO NAAM'].strip() or None

                school['rmc_region_code'] = int_or_none(row['RMC-REGIO CODE'].strip())

                yield school

    def parse_po_student_weight(self, response):
        """
        Primair onderwijs > Leerlingen
        Parse "01. Leerlingen basisonderwijs naar leerlinggewicht en per
                   vestiging het schoolgewicht en impulsgebied"
        """

        def parse_row(row):
            if row.has_key('VESTIGINSNUMMER'): # don't ask
                row['VESTIGINGSNUMMER'] = row['VESTIGINSNUMMER']

            # Datasets 2011 and 2012 suddenly changed these field names.
            if row.has_key('BRINNUMMER'):
                row['BRIN NUMMER'] = row['BRINNUMMER']

            if row.has_key('GEWICHT0.00'):
                row['GEWICHT 0'] = row['GEWICHT0.00']
            if row.has_key('GEWICHT0.30'):
                row['GEWICHT 0.3'] = row['GEWICHT0.30']
            if row.has_key('GEWICHT1.20'):
                row['GEWICHT 1.2'] = row['GEWICHT1.20']

            if row.has_key('GEWICHT 0.00'):
                row['GEWICHT 0'] = row['GEWICHT 0.00']
            if row.has_key('GEWICHT 0.30'):
                row['GEWICHT 0.3'] = row['GEWICHT 0.30']
            if row.has_key('GEWICHT 1.20'):
                row['GEWICHT 1.2'] = row['GEWICHT 1.20']

            brin = row['BRIN NUMMER'].strip()
            # Bypasses error coming from the 2011 dataset which contains and
            # empty row.
            if row['VESTIGINGSNUMMER'].strip():
                branch_id = int(row['VESTIGINGSNUMMER'])
            else:
                branch_id = 0

            weights = {
                'student_weight_0_0': int_or_none(row['GEWICHT 0'].strip()),
                'student_weight_0_3': int_or_none(row['GEWICHT 0.3'].strip()),
                'student_weight_1_2': int_or_none(row['GEWICHT 1.2'].strip()),
                'school_weight': int_or_none(row['SCHOOLGEWICHT'].strip()),
            }

            # The 2008 dataset doesn't contain the IMPULSGEBIED field.
            if row.has_key('IMPULSGEBIED'):
                if row['IMPULSGEBIED'].strip():
                    weights['impulse_area'] = bool(int(row['IMPULSGEBIED'].strip()))
                else:
                    weights['impulse_area'] = None

            yield (brin, branch_id), weights

        return self.dataset(response, self.make_item, 'weights_per_school', parse_row)

    def parse_po_student_age(self, response):
        """
        Primair onderwijs > Leerlingen
        Parse "02. Leerlingen basisonderwijs naar leeftijd"
        """

        for csv_url, reference_date in find_available_datasets(response).iteritems():
            reference_year = reference_date.year
            reference_date = str(reference_date)
            school_ids = {}
            ages_per_branch_by_student_weight = {}

            for row in parse_csv_file(csv_url):
                # Datasets 2011 and 2012 suddenly changed these field names.
                if row.has_key('BRINNUMMER'):
                    row['BRIN NUMMER'] = row['BRINNUMMER']
                if row.has_key('GEMEENTE NUMMER'):
                    row['GEMEENTENUMMER'] = row['GEMEENTE NUMMER']
                if row.has_key('VESTIGINGS NUMMER'):
                    row['VESTIGINGSNUMMER'] = row['VESTIGINGS NUMMER']
                if row.has_key('VESTIGINS NUMMER'):
                    row['VESTIGINGSNUMMER'] = row['VESTIGINS NUMMER']

                brin = row['BRIN NUMMER'].strip()
                branch_id = int(row['VESTIGINGSNUMMER'])
                school_id = '%s-%s' % (brin, branch_id)

                school_ids[school_id] = {
                    'brin': brin,
                    'branch_id': branch_id
                }

                # The 2012 dataset had fields like 'LEEFTIJD 4 JAAR' instead of
                # '4 JAAR'. This fixes the problem.
                for key in row.keys():
                    key_norm = key.replace('LEEFTIJD ', '')
                    row[key_norm] = row[key]

                # Remove leading/trailing spaces from field names.
                for key in row.keys():
                    row[key.strip()] = row[key]

                ages = {}
                possible_ages = '3 4 5 6 7 8 9 10 11 12 13 14'.split()
                for age in possible_ages:
                    if row.has_key('%s JAAR' % age):
                        if row['%s JAAR' % age].strip():
                            ages['age_%s' % age] = int(row['%s JAAR' % age])
                        else:
                            ages['age_%s' % age] = 0
                    # The 2011 dataset uses other field names.
                    elif row.has_key('LEEFTIJD.%s' % age):
                        if row['LEEFTIJD.%s' % age].strip():
                            ages['age_%s' % age] = int(row['LEEFTIJD.%s' % age])
                        else:
                            ages['age_%s' % age] = 0

                if school_id not in ages_per_branch_by_student_weight:
                    ages_per_branch_by_student_weight[school_id] = {}

                if row['GEWICHT'].strip():
                    weight = 'student_weight_%.1f' % float(row['GEWICHT'].strip().replace(',', '.').replace('.', '_'))
                else:
                    weight = None

                ages_per_branch_by_student_weight[school_id][weight] = ages

            for school_id, a_per_school in ages_per_branch_by_student_weight.iteritems():
                school = DuoPoBranch(
                    brin=school_ids[school_id]['brin'],
                    branch_id=school_ids[school_id]['branch_id'],
                    reference_year=reference_year,
                    ages_per_branch_by_student_weight_reference_url=csv_url,
                    ages_per_branch_by_student_weight_reference_date=reference_date,
                    ages_per_branch_by_student_weight=a_per_school
                )
                yield school

    def parse_po_born_outside_nl(self, response):
        """
        Primair onderwijs > Leerlingen
        Parse "09. Leerlingen basisonderwijs met een niet-Nederlandse achtergrond naar geboorteland"
        """

        for csv_url, reference_date in find_available_datasets(response).iteritems():
            reference_year = reference_date.year
            reference_date = str(reference_date)
            school_ids = {}
            students_by_origin = {}

            for row in parse_csv_file(csv_url):
                brin = row['BRIN NUMMER'].strip()
                branch_id = int(row['VESTIGINGSNUMMER'])
                school_id = '%s-%s' % (brin, branch_id)

                school_ids[school_id] = {
                    'brin': brin,
                    'branch_id': branch_id
                }

                # Remove leading/trailing spaces from field names and values.
                for key in row.keys():
                    row[key.strip()] = row[key].strip()

                origins = []
                for origin, country in settings['ORIGINS'].iteritems():
                    orig = {'country': country}
                    if row[origin].strip():
                        orig['students'] = int(row[origin].strip())
                    else:
                        orig['students'] = 0

                    origins.append(orig)

                if school_id not in students_by_origin:
                    students_by_origin[school_id] = origins

            for school_id, origs in students_by_origin.iteritems():
                school = DuoPoBranch(
                    brin=school_ids[school_id]['brin'],
                    branch_id=school_ids[school_id]['branch_id'],
                    reference_year=reference_year,
                    students_by_origin_reference_url=csv_url,
                    students_by_origin_reference_date=reference_date,
                    students_by_origin=origs
                )
                yield school

    def parse_po_pupil_zipcode_by_age(self, response):
        """
        Primair onderwijs > Leerlingen
        Parse "11. Leerlingen primair onderwijs per gemeente naar postcode leerling en leeftijd"
        """

        # For some reason, DUO decided to create a seperate file for each
        # municipality, zip them and only provide xls files.
        for zip_url, reference_date in find_available_datasets(response, extension='zip').iteritems():
            reference_year = reference_date.year
            reference_date = str(reference_date)

            for csv_file in extract_csv_files(zip_url):
                school_ids = {}
                student_residences = {}

                for row in csv_file :
                    # Remove leading/trailing spaces from field names and values.
                    for key in row.keys():
                        row[key.strip()] = row[key].strip()

                    brin = row['BRIN_NUMMER'].strip()
                    branch_id = int(row['VESTIGINGSNUMMER'])
                    school_id = '%s-%s' % (brin, branch_id)

                    school_ids[school_id] = {
                        'brin': brin,
                        'branch_id': branch_id
                    }

                    if school_id not in student_residences:
                        student_residences[school_id] = []

                    student_residence = {
                        'zip_code': row['POSTCODE_LEERLING'].strip(),
                        'ages': []
                    }

                    for age in range(3, 26):
                        if row.has_key('LEEFTIJD_%i_JAAR' % age):
                            student_residence['ages'].append({
                                'age': age,
                                # Find out why Sicco casts to a float before he
                                # cast to an int..
                                'students': int(float(row['LEEFTIJD_%i_JAAR' % age].strip()))
                            })

                    student_residences[school_id].append(student_residence)

                for school_id, residence in student_residences.iteritems():
                    school = DuoPoBranch(
                        brin=school_ids[school_id]['brin'],
                        branch_id=school_ids[school_id]['branch_id'],
                        reference_year=reference_year,
                        student_residences_reference_url=zip_url,
                        student_residences_reference_date=reference_date,
                        student_residences=residence
                    )

                yield school

    def parse_po_student_year(self, response):
        """
        Primair onderwijs > Leerlingen
        Parse "11. Leerlingen (speciaal) basisonderwijs per schoolvestiging naar leerjaar"
        """

        for csv_url, reference_date in find_available_datasets(response).iteritems():
            reference_year = reference_date.year
            reference_date = str(reference_date)
            school_ids = {}
            years_per_branch = {}

            for row in parse_csv_file(csv_url):
                # Datasets 2011 and 2012 suddenly changed these field names. (?)
                if row.has_key('BRINNUMMER'):
                    row['BRIN NUMMER'] = row['BRINNUMMER']
                if row.has_key('GEMEENTE NUMMER'):
                    row['GEMEENTENUMMER'] = row['GEMEENTE NUMMER']
                if row.has_key('VESTIGINGS NUMMER'):
                    row['VESTIGINGSNUMMER'] = row['VESTIGINGS NUMMER']
                if row.has_key('VESTIGINSNUMMER'): # don't ask
                    row['VESTIGINGSNUMMER'] = row['VESTIGINSNUMMER']

                brin = row['BRIN NUMMER'].strip()
                branch_id = 0 if not row['VESTIGINGSNUMMER'] else int(row['VESTIGINGSNUMMER'])
                school_id = '%s-%s' % (brin, branch_id)

                school_ids[school_id] = {
                    'brin': brin,
                    'branch_id': branch_id
                }

                # Remove leading/trailing spaces from field names.
                for key in row.keys():
                    row[key.strip()] = row[key]

                years = {}
                possible_years = '1 2 3 4 5 6 7 8'.split()
                for year in possible_years:
                    if row['LEERJAAR %s' % year].strip():
                        years['year_%s' % year] = int(row['LEERJAAR %s' % year])
                    else:
                        years['year_%s' % year] = 0
                # Is there a total student number from somewhere?
                # if row['TOTAAL'].strip():
                #         years['students'] = int(row['TOTAAL'])

                years_per_branch[school_id] = years

            for school_id, y_per_branch in years_per_branch.iteritems():
                school = DuoPoBranch(
                    brin=school_ids[school_id]['brin'],
                    branch_id=school_ids[school_id]['branch_id'],
                    reference_year=reference_year,
                    students_by_year_reference_url=csv_url,
                    students_by_year_reference_date=reference_date,
                    students_by_year=y_per_branch
                )
                yield school

    def parse_spo_students_by_birthyear(self, response):
        """
        Passend onderwijs > Leerlingen
        Parse: "05. Leerlingen speciaal (basis)onderwijs naar geboortejaar"
        """

        for csv_url, reference_date in find_available_datasets(response).iteritems():
            reference_year = reference_date.year
            reference_date = str(reference_date)

            for row in parse_csv_file(csv_url):
                # Datasets 2011 and 2012 suddenly changed these field names. (?)
                if row.has_key('BRINNUMMER'):
                    row['BRIN NUMMER'] = row['BRINNUMMER']
                if row.has_key('VESTIGINGS NUMMER'):
                    row['VESTIGINGSNUMMER'] = row['VESTIGINGS NUMMER']
                if row.has_key('VESTIGINSNUMMER'): # don't ask
                    row['VESTIGINGSNUMMER'] = row['VESTIGINSNUMMER']


                branch = DuoPoBranch()
                branch['brin'] = row['BRIN NUMMER'].strip()
                branch['branch_id'] = 0 if not row['VESTIGINGSNUMMER'] else int(row['VESTIGINGSNUMMER'])
                branch['reference_year']=reference_year

                # Should this be in root, or spo_students_by_birthyear?
                branch['spo_law'] = row['AANDUIDING WET']
                branch['spo_edu_type'] = row['SOORT PRIMAIR ONDERWIJS'] # possibly multiple with slash
                branch['spo_cluster'] = row['CLUSTER'] # i hope they don't use slashes

                # ignoring total
                students_by_birthyear = []
                for k, v in row.iteritems():
                    row_words = k.split('.')
                    # k is of the form 'aantal.2005' or just the year (yes, really)
                    if row_words[-1].isdigit() and v.isdigit() and int(v)>0 :
                        students_by_birthyear.append({
                            'birthyear' : int(row_words[-1]),
                            'students' : int(v),
                        })


                branch['spo_students_by_birthyear'] = students_by_birthyear
                branch['spo_students_by_birthyear_reference_url'] = csv_url
                branch['spo_students_by_birthyear_reference_date'] = reference_date

                yield branch

    def parse_spo_students_by_edu_type(self, response):
        """
        Primair onderwijs > Leerlingen
        Parse "06. Leerlingen speciaal (basis)onderwijs naar onderwijssoort"
        """

        def parse_row(row):
            # strip leading and trailing whitespace.
            for key in row.keys():
                value = (row[key] or '').strip()
                row[key] = value or None
                row[key.strip()] = value or None

            # Datasets 2011 and 2012 suddenly changed these field names.
            if row.has_key('BRINNUMMER'):
                row['BRIN NUMMER'] = row['BRINNUMMER']
            if row.has_key('VESTIGINGS NUMMER'):
                row['VESTIGINGSNUMMER'] = row['VESTIGINGS NUMMER']
            if row.has_key('VESTIGINSNUMMER'): # don't ask
                row['VESTIGINGSNUMMER'] = row['VESTIGINSNUMMER']

            if row.has_key('INDICATIE SPECIAAL (BASIS)ONDERWIJS'): # really now
                row['INDICATIE SPECIAL BASIS ONDERWIJS'] = row['INDICATIE SPECIAAL (BASIS)ONDERWIJS']

            brin = row['BRIN NUMMER'].strip()
            if row['VESTIGINGSNUMMER'].strip():
                branch_id = int(row['VESTIGINGSNUMMER'])
            else:
                branch_id = 0

            yield (brin, branch_id), {
                'spo_indication' : row['INDICATIE SPECIAL BASIS ONDERWIJS'],
                'spo' : int(row['SBAO'] or 0),
                'so' : int(row['SO'] or 0),
                'vso' : int(row['VSO'] or 0),
            }

        return self.dataset(response, self.make_item, 'spo_students_by_edu_type', parse_row)


    def parse_po_students_by_advice(self, response):
        """
        Primair onderwijs > Leerlingen
        Parse "12. Leerlingen (speciaal) basisonderwijs per schoolvestiging naar schooladvies"

        TODO: compare to owinsp PRIMARY_SCHOOL_ADVICES_STRUCTURES
        """

        def parse_row(row):
            # strip leading and trailing whitespace.
            for key in row.keys():
                value = (row[key] or '').strip()
                row[key] = value or None
                row[key.strip()] = value or None

            # Datasets 2011 and 2012 suddenly changed these field names.
            if row.has_key('BRINNUMMER'):
                row['BRIN NUMMER'] = row['BRINNUMMER']
            if row.has_key('VESTIGINGS NUMMER'):
                row['VESTIGINGSNUMMER'] = row['VESTIGINGS NUMMER']
            if row.has_key('VESTIGINSNUMMER'): # don't ask
                row['VESTIGINGSNUMMER'] = row['VESTIGINSNUMMER']

            if row['BRIN NUMMER']:
                brin = row['BRIN NUMMER'].strip()
                branch_id = int(row['VESTIGINGSNUMMER'].strip() or 0)

                yield (brin, branch_id), {
                    # TODO intOrNone
                    'vso' : int(row['VSO'] or 0),
                    'pro' : int(row['PrO'] or 0),
                    'vmbo_bl' : int(row['VMBO BL'] or 0),
                    'vmbo_bl_kl' : int(row['VMBO BL-KL'] or 0),
                    'vmbo_kl' : int(row['VMBO KL'] or 0),
                    'vmbo_kl_gt' : int(row['VMBO KL-GT'] or 0),
                    'vmbo_gt' : int(row['VMBO GT'] or 0),
                    'vmbo_gt_havo' : int(row['VMBO GT-HAVO'] or 0),
                    'havo' : int(row['HAVO'] or 0),
                    'havo_vwo' : int(row['HAVO-VWO'] or 0),
                    'vwo' : int(row['VWO'] or 0),
                    'unknown' : int(row['ONBEKEND'] or 0),
                    }

        return self.dataset(response, self.make_item, 'spo_students_by_advice', parse_row)

    def parse_po_students_in_BRON(self, response):
        """
        Primair onderwijs > Leerlingen
        Parse "10. Leerlingen zoals geregistreerd in BRON tot 26-10-2013"
        """
        # number of header rows to merge
        merge_header = {
            'Leerlingen (v)so': 3,
            'Leerlingen bao': 2,
            'Leerlingen sbao': 2,
        }
        translate = {
            'Aantal bekostigd':
                'financed',
            'Aantal niet-bekostigd':
                'non-financed',
            'Actief op 1-10-13':
                'total',
            'Ambulante begeleiding':
                'ambulatory_guidance',
            'Bekostigd op 1-10-13':
                'financed',
            'Inschrijvingen actief op 1-10-13':
                'total',
            'Leerlingen teruggeplaatst tussen 1-10-2012 en 1-10-2013':
                'reintroduced',
            'Niet-bekostigd op 1-10-13':
                'non-financed',
            'Totaal aantal':
                'total',
            'Waarvan bekostigde inschrijvingen op 1-10-13':
                'financed',
            'Waarvan niet-bekostigde inschrijvingen op 1-10-13':
                'non-financed',
            'Inschrijvingen SO/VSO':
                'enrollments',
            'AANTAL LEERLINGEN TOTAAL':
                'total',
            'BEGELEID BO/SBO':
                'guided_bo_bso',
            'BEGELEID VO':
                'guided_vo',
            'CUMI':
                'cumi',
            'NOAT':
                'noat',
            'SOORT PRIMAIR ONDERWIJS':
                'po_type',
            'TERUGGEPLAATST BO/SBO':
                'reintroduced_bo_sbo',
            'TERUGGEPLAATST VO':
                'reintroduced_vo',
            'VESTIGING ACTIEF OP PEILDATUM':
                'branch_active',
        }

        for xls_url, reference_date in find_available_datasets(response, extension='xls').iteritems():
            reference_year = reference_date.year # different years in document
            reference_date = str(reference_date)
            school_ids = {}
            students_in_BRON_per_school = {}

            # download manually
            xls_file = requests.get(xls_url)
            xls = cStringIO.StringIO(xls_file.content)
            with open(devnull, 'w') as OUT:
                wb = xlrd.open_workbook(file_contents=xls.read(), logfile=OUT)

            for sheet_name in wb.sheet_names():
                if sheet_name in merge_header:
                    sh = wb.sheet_by_name(sheet_name)

                    # merge the header rows into a list of triples
                    header = zip(*[extend_to_blank(sh.row_values(h)) for h in xrange(merge_header[sheet_name])])
                    if merge_header[sheet_name] == 2:
                        header = [(True,a,b) for a,b in header]

                    for rownum in xrange(merge_header[sheet_name], sh.nrows):
                        data = sh.row_values(rownum)
                        per_school = {}

                        def row_to_dict(key, val):
                            """ Parse the key of this row & make a nice dict """
                            ks = key.split()
                            if 'JAAR' in ks:
                                return 'by_age', {
                                    'age_range': '<%s'%ks[-2] if ks[0] == 'JONGER' \
                                            else '>%s'%ks[0] if ks[-1] == 'OUDER' \
                                            else '%s-%s'%(ks[0], ks[2]),
                                    'students': val,
                                }
                            if ks[0] == 'GEWICHT':
                                return 'by_weight', {
                                    'weight': float(ks[1]),
                                    'students': val,
                                }
                            return None, None

                        # Loop over columns & build a translated nested dict
                        for i,(a,b,c) in enumerate(header):
                            a = translate[a] if a in translate else a
                            b = translate[b] if b in translate else b
                            if a and b:
                                per_school[a] = {} if a not in per_school else per_school[a]
                                per_school[a][b] = {} if b not in per_school[a] else per_school[a][b]
                                if c in translate:
                                    per_school[a][b][translate[c]] = int(data[i])
                                else:
                                    k,v = row_to_dict(c, int(data[i]))
                                    if k:
                                        if k not in per_school[a][b]:
                                            per_school[a][b][k] = []
                                        per_school[a][b][k].append(v)
                            elif a or b:
                                a = a or b
                                per_school[a] = {} if a not in per_school else per_school[a]
                                per_school[a][translate[c] if c in translate else c] = data[i]
                            else:
                                per_school[translate[c] if c in translate else c] = data[i]

                        # un-nest the hierarchy if it's actually only 2 deep
                        if merge_header[sheet_name] == 2:
                            per_school = per_school[True]

                        per_school['branch_active'] = (per_school['branch_active']=='ja')

                        brin = per_school.pop('BRIN NUMMER')
                        branch_id = int(per_school.pop('VESTIGINGSNUMMER'))
                        school_id = '%s-%s' % (brin, branch_id)
                        school_ids[school_id] = {
                            'brin': brin,
                            'branch_id': branch_id
                        }

                        if school_id not in students_in_BRON_per_school:
                            students_in_BRON_per_school[school_id] = []

                        students_in_BRON_per_school[school_id].append(per_school)

            for school_id, per_school in students_in_BRON_per_school.iteritems():
                school = DuoPoBranch(
                    brin=school_ids[school_id]['brin'],
                    branch_id=school_ids[school_id]['branch_id'],
                    reference_year=reference_year,
                    students_in_BRON_reference_url=xls_url,
                    students_in_BRON_reference_date=reference_date,
                    students_in_BRON=per_school
                )
                yield school

    def parse_po_student_flow(self, response):
        """
        Stroominformatie > Doorstromers
        05. Doorstromers van primair naar voortgezet onderwijs
        """

        tables = []
        for csv_url, reference_date in find_available_datasets(response).iteritems():
            csv_file = requests.get(csv_url)
            csv_file.encoding = 'cp1252'
            csv_file = cStringIO.StringIO(csv_file.content
                  .decode('cp1252').encode('utf8'))
            tables.append( (csv_file, reference_date, csv_url) )

        # add local file
        if self.add_local_table is not None:
            try:
                reference_date = datetime.strptime(basename(self.add_local_table), '%Y-%m-%d.csv').date()
                tables.append( (file(self.add_local_table), reference_date, None) )
            except Exception as ex:
                print 'Could not add', self.add_local_table, ':',  ex

        for table, reference_date, csv_url in tables:
            reference_year = reference_date.year
            reference_date = str(reference_date)
            flow_per_branch = get_student_flow(table, for_po=True)

            for (brin, branch_id), per_school in flow_per_branch.iteritems():
                school = DuoPoBranch(
                    brin=brin,
                    branch_id=branch_id,
                    reference_year=reference_year,
                    student_flow_reference_url=csv_url,
                    student_flow_reference_date=reference_date,
                    student_flow=per_school
                )
                yield school


class DuoPaoCollaborationsSpider(DuoSpider):
    name = 'duo_pao_collaborations'

    def __init__(self, *args, **kwargs):
        self.requests = {
            'passendow/Adressen/Adressen/passend_po_1.asp':
                self.parse_collaborations,
            'passendow/Adressen/Adressen/passend_po_3.asp':
                self.parse_collaborations,
            'passendow/Adressen/Adressen/passend_vo_1.asp':
                self.parse_collaborations,
            'passendow/Adressen/Adressen/passend_vo_7.asp':
                self.parse_collaborations,
        }
        DuoSpider.__init__(self, *args, **kwargs)

    def parse_collaborations(self, response):
        """
        Passend onderwijs > Adressen
        Parse: "01. Adressen samenwerkingsverbanden lichte ondersteuning primair onderwijs"
        Parse: "03. Adressen samenwerkingsverbanden passend onderwijs, primair onderwijs"
        Parse: "05. Adressen samenwerkingsverbanden lichte ondersteuning, voortgezet onderwijs"
        Parse: "07. Adressen samenwerkingsverbanden passend onderwijs, voortgezet onderwijs"
        """
        # Fields that do not need additonal processing
        collaboration_fields = {
            'SAMENWERKINGSVERBAND': 'name'
        }

        for csv_url, reference_date in find_available_datasets(response).iteritems():
            reference_year = reference_date.year
            reference_date = str(reference_date)
            for row in parse_csv_file(csv_url):

                collaboration = DuoPaoCollaboration()

                cid = row['ADMINISTRATIENUMMER'].strip()
                if '-' in cid:
                    int_parts = map(int_or_none, cid.split('-'))
                    if any([i == None for i in int_parts]):
                        cid = '-'.join(map(str, int_parts))
                collaboration['collaboration_id'] = cid

                collaboration['address'] = {
                    'street': row['ADRES'].strip() or None,
                    'city': row['PLAATSNAAM'].strip() or None,
                    'zip_code': row['POSTCODE'].replace(' ', '') or None
                }

                collaboration['correspondence_address'] = {
                    'street': row['CORRESPONDENTIEADRES'],
                    'city': row['PLAATS CORRESPONDENTIEADRES'],
                    'zip_code': row['POSTCODE CORRESPONDENTIEADRES'].replace(' ', '')
                }

                for field, field_norm in collaboration_fields.iteritems():
                    collaboration[field_norm] = row[field]

                collaboration['reference_year'] = reference_year
                collaboration['ignore_id_fields'] = ['reference_year']

                yield collaboration
