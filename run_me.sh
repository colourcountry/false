#!/bin/bash

export FALSE_OUT=pub
export FALSE_URL_BASE=http://localhost:8818
export FALSE_SRC=test
export FALSE_TEMPLATES=templates
export FALSE_ID_BASE=http://id.colourcountry.net/false-test/
export FALSE_HOME_SITE=http://id.colourcountry.net/false-test/Site

rm -fr "$FALSE_OUT"
mkdir -p "$FALSE_OUT/ipfs"

./convert_images.sh "$FALSE_SRC"

export FALSE_HOME_PAGE=`python3 false.py`
cp -avu static "$FALSE_OUT/static"
python3 server.py "$FALSE_HOME_PAGE"

