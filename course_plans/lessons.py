"""Reviewed original teaching examples; course mapping is supplied by curriculum.py.

Examples use invented data, never answers to course assessments. Resource URLs
were opened during authoring on 2026-09-15. No network/model call is needed to build.
"""
RESOURCES = {
 'quarto': ('Quarto: get started', 'https://quarto.org/docs/get-started/'),
 'polars': ('Polars: getting started', 'https://docs.pola.rs/user-guide/getting-started/'),
 'ml': ('scikit-learn: getting started', 'https://scikit-learn.org/stable/getting_started.html'),
 'r': ('R for Data Science: chapter directory', 'https://r4ds.hadley.nz/'),
 'sql': ('MySQL: official tutorial', 'https://dev.mysql.com/doc/refman/8.4/en/tutorial.html'),
 'pbi': ('Microsoft: get started with Power BI Desktop', 'https://learn.microsoft.com/en-us/power-bi/fundamentals/desktop-getting-started'),
 'dax': ('Microsoft: DAX overview', 'https://learn.microsoft.com/en-us/dax/dax-overview'),
}
import json
from pathlib import Path

_data_file = Path(__file__).parent.parent / 'data' / 'lesson_content.json'
with open(_data_file, 'r') as f:
    LESSONS = json.load(f)
