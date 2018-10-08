#!/usr/bin/python3

import rdflib
from rdflib.namespace import RDF, DC, SKOS, OWL
import sys, logging, os, uuid
import ipfsapi

IPFS_CLIENT = ipfsapi.connect('127.0.0.1',5001)

TTL_BASE = "../entity"

FILE_TYPES = { ".html": "text/html", ".txt": "text/plain", ".md": "text/markdown" }

CC = rdflib.Namespace("http://www.colourcountry.net/thing/")
IPFS = rdflib.Namespace("/ipfs/")

g = rdflib.Graph()
gg = rdflib.Graph()
gg.bind('cc', CC)
gg.bind('dc', DC)
gg.bind('skos', SKOS)
gg.bind('owl', OWL)

for path, dirs, files in os.walk(TTL_BASE):
  for f in files:
      if f.endswith('.ttl'):
          print("Loading %s from %s" % (f,path))
          g.load(os.path.join(path,f), format='ttl')

DOCS = {}
ENTS = {}

for s, p, o in g:
    if s not in ENTS:
        ENTS[s] = CC[str(uuid.uuid4())]
    if p == RDF.type:
        if o == CC['Document']:
            DOCS[s] = ENTS[s]

for s, p, o in g:
    if o in ENTS:
        o = ENTS[o]

    if p == CC['markdown']:
        r = IPFS_CLIENT.add_bytes(o.encode('utf-8'))
        blob_id = CC[str(uuid.uuid4())]
        gg.add((blob_id, RDF.type, CC['Media']))
        gg.add((blob_id, CC['mediaType'], rdflib.Literal('text/markdown')))
        gg.add((blob_id, CC['charset'], rdflib.Literal('utf-8')))
        gg.add((blob_id, CC['data'], IPFS[r]))
        gg.add((DOCS[s], CC['content'], blob_id))
        del(DOCS[s]) # don't need to look for a file
    else:
        gg.add((ENTS[s], p, o))

for s, ent_id in ENTS.items():
    if not s.startswith("file://"):
        gg.add((s, OWL['sameAs'], ent_id))

for s, doc_id in DOCS.items():
    for t, mime in FILE_TYPES.items():
        fn = s+t
        if fn.startswith("file://"):
            fn = fn[7:]
        try:
            r = IPFS_CLIENT.add(fn)
            blob_id = CC[str(uuid.uuid4())]
            gg.add((blob_id, RDF.type, CC['Media']))
            gg.add((blob_id, CC['mediaType'], rdflib.Literal(mime)))
            gg.add((blob_id, CC['charset'], rdflib.Literal('utf-8')))
            gg.add((blob_id, CC['data'], IPFS[r['Hash']]))
            gg.add((doc_id, CC['content'], blob_id))
        except ipfsapi.exceptions.ConnectionError:
            logging.warn("Couldn't open %s" % fn)

print(gg.serialize(format='ttl').decode('utf-8'))
