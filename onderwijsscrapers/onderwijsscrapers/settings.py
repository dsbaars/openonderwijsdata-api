import os
import exporters
import yaml

yaml_stream = open("../config/config.yaml")
config = yaml.load(yaml_stream)

BOT_NAME = 'onderwijsscrapers'
BOT_VERSION = '1.0'

SPIDER_MODULES = ['onderwijsscrapers.spiders']
ITEM_PIPELINES = ['onderwijsscrapers.pipelines.OnderwijsscrapersPipeline']
NEWSPIDER_MODULE = 'onderwijsscrapers.spiders'
USER_AGENT = '%s/%s' % (BOT_NAME, BOT_VERSION)

# Autothrottling settings
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_DEBUG = False

# Full filesystem path to the project
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# Path to the file that holds all zipcodes (first 4 digits!). This file
# is used for searching in the toezichtkaart.owinsp.nl databse.
ZIPCODES = os.path.join(PROJECT_ROOT, 'zips.txt')

# Path to the file with all PO addresses we need in the owinsp spider to search
# for po_schools
PO_ADDRESSES = os.path.join(PROJECT_ROOT, 'po_addresses.csv')

NO_BRIN = os.path.join(PROJECT_ROOT, 'no_brins.txt')
MULTIPLE_SCHOOLS_FOR_BRIN = os.path.join(PROJECT_ROOT, 'multiple_brins.txt')

SCHOOLVO_URL = 'http://www.schoolvo.nl/'

# Mapping of DUO countries of origin to more acceptable variants
ORIGINS = {
    'ARUBA': 'aruba',
    'DE MOLUKSE EILANDEN': 'maluku_islands',
    'GIEKENLAND': 'greece',
    'ITALIE': 'italy',
    'KAAPVERDIE': 'cape_verde',
    'MAROKKO': 'morocco',
    'NEDERLANDSE ANTILLEN': 'netherlands_antilles',
    'NIET-ENGELSTALIGEN': 'non_english_speaking_countries',
    'PORTUGAL': 'portugal',
    'SPANJE': 'spain',
    'SURINAME': 'suriname',
    'TUNESIE': 'tunisia',
    'TURKIJE': 'turkey',
    'VLUCHTELINGEN': 'refugees',
    'VML.JOEGOSLAVIE': 'former_yugoslavia',
}

# Directory to which scrape results should be saved (in case the file
# exporter is used).
EXPORT_DIR = os.path.join(PROJECT_ROOT, 'export')

# Available methods are 'elasticsearch' and 'file'
EXPORT_METHODS = {
    'elasticsearch': {
        'exporter': exporters.ElasticSearchExporter,
        'options': {
            'url': 'blackbox:9200'
        }
    }
}

from validation.duo import (DuoVoSchool, DuoVoBoard, DuoVoBranch, DuoPoSchool,
                            DuoPoBoard, DuoPoBranch, DuoPaoCollaboration)
from validation.schoolvo import SchoolVOBranch
from validation.owinsp import (OnderwijsInspectieVoBranch, OnderwijsInspectiePoBranch)
from validation.ocw import OCWPoBranch

EXPORT_SETTINGS = {
    'po.owinsp.nl': {
        'validate': True,
        'schema': OnderwijsInspectiePoBranch,
        'validation_index': 'onderwijsdata_validation',
        'geocode': False,
        'geocode_fields': ['address'],
        'index': 'onderwijsinspectie',
        'doctype': 'po_branch',
        'id_fields': ['owinsp_id', 'brin']
    },
    'vo.owinsp.nl': {
        'validate': True,
        'schema': OnderwijsInspectieVoBranch,
        'validation_index': 'onderwijsdata_validation',
        'geocode': False,
        'geocode_fields': ['address'],
        'index': 'onderwijsinspectie',
        'doctype': 'vo_branch',
        'id_fields': ['brin', 'branch_id']
    },
    'schoolvo.nl': {
        'validate': True,
        'schema': SchoolVOBranch,
        'validation_index': 'onderwijsdata_validation',
        'geocode': True,
        'geocode_fields': ['address'],
        'index': 'schoolvo',
        'doctype': 'vo_branch',
        'id_fields': ['brin', 'branch_id']
    },
    'duo_vo_branches': {
        'validate': True,
        'schema': DuoVoBranch,
        'validation_index': 'onderwijsdata_validation',
        'geocode': True,
        'geocode_fields': ['address'],
        'index': 'duo',
        'doctype': 'vo_branch',
        'id_fields': ['reference_year', 'brin', 'branch_id']
    },
    'duo_vo_boards': {
        'validate': True,
        'schema': DuoVoBoard,
        'validation_index': 'onderwijsdata_validation',
        'geocode': True,
        'geocode_fields': ['address', 'correspondence_address'],
        'index': 'duo',
        'doctype': 'vo_board',
        'id_fields': ['reference_year', 'board_id']
    },
    'duo_vo_schools': {
        'validate': True,
        'schema': DuoVoSchool,
        'validation_index': 'onderwijsdata_validation',
        'geocode': True,
        'geocode_fields': ['address', 'correspondence_address'],
        'index': 'duo',
        'doctype': 'vo_school',
        'id_fields': ['reference_year', 'brin']
    },
    'duo_po_boards': {
        'validate': True,
        'schema': DuoPoBoard,
        'validation_index': 'onderwijsdata_validation',
        'geocode': True,
        'geocode_fields': ['address', 'correspondence_address'],
        'index': 'duo',
        'doctype': 'po_board',
        'id_fields': ['reference_year', 'board_id']
    },
    'duo_po_schools': {
        'validate': True,
        'schema': DuoPoSchool,
        'validation_index': 'onderwijsdata_validation',
        'geocode': True,
        'geocode_fields': ['address', 'correspondence_address'],
        'index': 'duo',
        'doctype': 'po_school',
        'id_fields': ['reference_year', 'brin']
    },
    'duo_po_branches': {
        'validate': True,
        'schema': DuoPoBranch,
        'validation_index': 'onderwijsdata_validation',
        'geocode': False,
        'geocode_fields': ['address', 'correspondence_address'],
        'index': 'duo',
        'doctype': 'po_branch',
        'id_fields': ['reference_year', 'brin', 'branch_id']
    },
    'duo_pao_collaborations': {
        'validate': True,
        'schema': DuoPaoCollaboration,
        'validation_index': 'onderwijsdata_validation',
        'geocode': False,
        'geocode_fields': ['address', 'correspondence_address'],
        'index': 'duo',
        'doctype': 'pao_collaboration',
        'id_fields': ['reference_year', 'collaboration_id']
    },
    'ocw_po_branches': {
        'validate': False,
        'schema': OCWPoBranch,
        'validation_index': 'onderwijsdata_validation',
        'geocode': False,
        'geocode_fields': ['address'],
        'index': 'ocw',
        'doctype': 'po_branch',
        'id_fields': ['reference_year', 'brin', 'branch_id']
    },
    'dans_vo_branches': {
        'validate': False,
        'schema': None,
        'validation_index': 'onderwijsdata_validation',
        'geocode': False,
        'geocode_fields': [],
        'index': 'dans',
        'doctype': 'vo_branch',
        'id_fields': ['brin', 'branch_id']
    },
}

# Allow all settings to be overridden by a local file that is not in
# the VCS.
try:
    from local_settings import *
except ImportError:
    pass
