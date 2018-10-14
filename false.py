#!/usr/bin/python3

import rdflib
from rdflib.namespace import RDF, DC, SKOS, OWL
import sys, logging, os, uuid, re, urllib.parse
import ipfsapi
import jinja2, markdown
import pprint
from triplate import *

FALSE_SRC = os.environ["FALSE_SRC"]
FALSE_URL_BASE = os.environ["FALSE_URL_BASE"]
FALSE_OUT = os.environ["FALSE_OUT"]
FALSE_TEMPLATES = os.environ["FALSE_TEMPLATES"]

IPFS_CLIENT = ipfsapi.connect('127.0.0.1',5001)
FILE_TYPES = { ".html": "text/html", ".txt": "text/plain", ".md": "text/markdown" }
CC = rdflib.Namespace("http://www.colourcountry.net/thing/")
IPFS = rdflib.Namespace("/ipfs/")


def addRendition(g, doc_id, blob, **properties):
    r = IPFS_CLIENT.add_bytes(blob)
    blob_id = IPFS[r]
    g.add((blob_id, RDF.type, CC['Media']))
    for k, v in properties.items():
        g.add((blob_id, CC[k], v))
    g.add((doc_id, CC['rendition'], blob_id))
    return blob_id


def buildGraph(g):
    gg = rdflib.Graph()
    gg.bind('cc', CC)
    gg.bind('dc', DC)
    gg.bind('skos', SKOS)
    gg.bind('owl', OWL)
    gg.bind('ipfs', '/ipfs/')

    DOCS = {}
    ENTS = {}

    for s, p, o in g:
        if s not in ENTS:
            ENTS[s] = s
        if p == RDF.type:
            if o == CC['Document']:
                ENTS[s] = CC[os.path.basename(s)]
                DOCS[s] = ENTS[s]

    for s, p, o in g:
        if o in ENTS:
            o = ENTS[o]

        if p == CC['markdown']:
            blob_id = addRendition(gg, DOCS[s], o.encode('utf-8'),
                mediaType=rdflib.Literal('text/markdown'),
                charset=rdflib.Literal('utf-8')
            )
            del(DOCS[s]) # don't need to look for a file
        else:
            gg.add((ENTS[s], p, o))

    for s, doc_id in DOCS.items():
        for t, mime in FILE_TYPES.items():
            fn = os.path.basename(s+t)
            try:
                o = open(os.path.join(FALSE_SRC,fn),'r').read()
                blob_id = addRendition(gg, DOCS[s], o.encode('utf-8'),
                    mediaType=rdflib.Literal(mime),
                    charset=rdflib.Literal('utf-8')
                    )
            except IOError:
                logging.warn("Couldn't open %s" % fn)
    return gg

def getDestPath(e, eType):
    return os.path.join(FALSE_OUT, eType.safe, e)

def publish(g):
    md = markdown.Markdown()
    tg = TemplatableGraph(g)

    for e in tg.entities:
        for eType in tg.entities[e].rdf_type:
            try:
                with open(os.path.join(FALSE_TEMPLATES,eType.safe),'r') as f:
                    t = jinja2.Template(f.read())
            except IOError as err:
                logging.debug("No template for %s: %s" % (eType.safe, err))
                continue

            dest = getDestPath(e, eType)
            logging.warn('Building %s' % dest)

            for r in tg.entities[e].cc_rendition:
                mt = r.cc_mediaType
                enc = r.cc_charset.pop()

                if rdflib.Literal('text/markdown') in mt:
                    tg.add(tg.entities[e].id, CC.html, rdflib.Literal(md.convert(IPFS_CLIENT.cat(r.id).decode(enc))))
                    content = t.render(tg.entities[e].po)
                elif rdflib.Literal('text/html') in mt:
                    content = IPFS_CLIENT.cat(r.id).decode(enc)
                else:
                    spo[s]['__html__'] = '<object type="%s" href="%s"></object>' % (str(mt.pop()), r)

            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest,'w') as f:
                    f.write(content)
            except IOError as e:
                logging.warn("Couldn't write rendering for %s: %s" % (dest, e))
                continue


if __name__=="__main__":
    g = rdflib.Graph()
    for path, dirs, files in os.walk(FALSE_SRC):
      for f in files:
          if f.endswith('.ttl'):
              print("Loading %s from %s" % (f,path))
              g.load(os.path.join(path,f), format='ttl')
    publish(buildGraph(g))
