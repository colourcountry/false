#!/bin/bash

export FALSE_SRC=test
export FALSE_OUT=pub
export FALSE_TEMPLATES=templates
export FALSE_URL_BASE=file://`pwd`/

rm -fr "$FALSE_OUT"

python3 ./false.py

