#!/bin/bash

export FALSE_OUT=pub
export FALSE_URL_BASE=http://localhost:8818
export FALSE_SRC=test
export FALSE_TEMPLATES=templates

rm -fr "$FALSE_OUT"
mkdir -p "$FALSE_OUT/ipfs"

python3 false.py
cp -avu static "$FALSE_OUT/static"
python3 server.py

