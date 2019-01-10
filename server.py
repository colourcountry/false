#!/usr/bin/python3

from bottle import route, run, static_file, redirect, response
import os, sys

@route('/')
def root():
    return redirect(sys.argv[1])

@route('/favicon.ico')
def fav():
    return static_file('static/favicon.ico', root=os.path.join(os.getcwd(),'pub'))

@route('/<fn:path>')
def serve(fn):
    return static_file(fn, root=os.path.join(os.getcwd(),'pub'))

if sys.argv[1]:
    run(host='localhost', port=8818)

