#!/usr/bin/python3

from bottle import route, run, static_file, redirect
import os

@route('/')
def root():
    # FIXME: get the start page from the build process somehow
    return redirect('/f_Site/f_asPage/lit_http___www_colourcountry_net_id_site_Site.html')

@route('/<fn:path>')
def serve(fn):
    return static_file(fn, root=os.path.join(os.getcwd(),'pub'))

run(host='localhost', port=8818)

