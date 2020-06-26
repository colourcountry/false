#!/bin/bash

export FALSE_OUT=pub
export FALSE_URL_BASE=http://localhost:8818
export FALSE_SRC=test
export FALSE_TEMPLATES=templates
export FALSE_WORK_DIR=build
export FALSE_ID_BASE=http://id.colourcountry.net/false-test/
export FALSE_HOME_SITE=http://id.colourcountry.net/false-test/site
export FALSE_LOG_FILE=false.log


rm -f "$FALSE_LOG_FILE"
rm -fr "$FALSE_OUT"
mkdir -p "$FALSE_OUT/ipfs"

#python3 ./prepare_media.sh "$FALSE_SRC"
cp -avu static "$FALSE_OUT/static"

export FALSE_HOME_PAGE=`time python3 false.py`

#python3 server.py "$FALSE_HOME_PAGE"
