#!/bin/bash

export FALSE_SRC=test
export FALSE_OUT=pub
export FALSE_TEMPLATES=templates
export FALSE_URL_BASE=http://localhost:8818

rm -fr "$FALSE_OUT"

python3 ./false.py
cp -avu static $FALSE_OUT/_static
python3 ./server.py

