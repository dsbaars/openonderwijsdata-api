import os
import exporters
import yaml

yaml.add_constructor
project_root_pattern = re.compile(r'\%\%PROJECT_ROOT\%\%')
yaml.add_implicit_resolver(u'!project_root', project_root_pattern)

# load configuration
with open(os.path.join(os.path.dirname(__file__), "../../app/config.yaml"), 'r') as f:
    config = yaml.load(f)

# BOT_NAME = 'onderwijsscrapers'
# BOT_VERSION = '1.0'

SPIDER_MODULES = ['onderwijsscrapers.spiders']
ITEM_PIPELINES = {
    'onderwijsscrapers.pipelines.OnderwijsscrapersPipeline': 1
}
NEWSPIDER_MODULE = 'onderwijsscrapers.spiders'

# Autothrottling settings
AUTOTHROTTLE_ENABLED = config['scraper']['scrapy']['autothrottle_enabled']
AUTOTHROTTLE_DEBUG = config['scraper']['scrapy']['autothrottle_debug']

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
ORIGINS = config['scraper']['origins']

# Directory to which scrape results should be saved (in case the file
# exporter is used).
EXPORT_DIR = os.path.join(PROJECT_ROOT, 'export')

# Available methods are 'elasticsearch' and 'file'
EXPORT_METHODS = config['scraper']['export_methods']

from validation.duo import (DuoVoSchool, DuoVoBoard, DuoVoBranch, DuoPoSchool,
                            DuoPoBoard, DuoPoBranch, DuoPaoCollaboration,
                            DuoMboBoard, DuoMboInstitution,
                            DuoHoBoard, DuoHoInstitution)
from validation.schoolvo import SchoolVOBranch
from validation.owinsp import (OnderwijsInspectieVoBranch, OnderwijsInspectiePoBranch)
from validation.ocw import OCWPoBranch

EXPORT_SETTINGS = config['scraper']['sources']

# Allow all settings to be overridden by a local file that is not in
# the VCS.
try:
    from local_settings import *
except ImportError:
    pass
