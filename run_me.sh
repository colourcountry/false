#!/bin/bash

# Dirs starting with . or _ will not be trawled for TTL by FALSE.
# FALSE's own output dirs should always be protected like this (or be safely out of the way), as they contain TTL.

export FALSE_OUT=_pub
export FALSE_URL_BASE=http://localhost:8818
export FALSE_SRC=src
export FALSE_TEMPLATES=templates
export FALSE_WORK_DIR=_build # because this outputs some TTL, it should be hidden with a _
export FALSE_ID_BASE=http://id.colourcountry.net/2018/
export FALSE_HOME_SITE=http://id.colourcountry.net/2018/false-test
export FALSE_LOG_FILE=false.log


rm -f "$FALSE_LOG_FILE"
rm -fr "$FALSE_OUT"
mkdir -p "$FALSE_OUT/ipfs"

#python3 ./prepare_media.sh "$FALSE_SRC"
cp -avu static "$FALSE_OUT/static"

export FALSE_HOME_PAGE=`time python3 false.py`

python3 server.py "$FALSE_HOME_PAGE"
