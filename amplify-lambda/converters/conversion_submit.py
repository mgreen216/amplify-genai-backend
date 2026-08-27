"""Authenticated API wrapper for document conversion submissions.

The S3 conversion worker intentionally lives in ``docconverter`` without the
application dependency layer so it can fit alongside the large Pandoc layer.
Only this API entry point needs the shared validation dependencies.
"""

from common.validate import validated
from converters.docconverter import submit_conversion_job


convert_endpoint = validated("convert")(submit_conversion_job)
