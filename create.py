#!/usr/bin/python3

import rdflib
from rdflib.namespace import RDF, DC, SKOS, OWL
import sys, logging, os, uuid, re
import ipfsapi
import jinja2, markdown

IPFS_CLIENT = ipfsapi.connect('127.0.0.1',5001)
TTL_BASE = "test"
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
                o = open(os.path.join(TTL_BASE,fn),'r').read()
                blob_id = addRendition(gg, DOCS[s], o.encode('utf-8'),
                    mediaType=rdflib.Literal(mime),
                    charset=rdflib.Literal('utf-8')
                    )
            except IOError:
                logging.warn("Couldn't open %s" % fn)
    return gg

def safePath(g,p):
    for (px, n) in g.namespaces():
        if p.startswith(n):
            return px+'_'+p[len(n):]
    return re.sub('[^A-Za-z]','_',p)

class GhostSet(set):
    '''A set that converts to string as just the concatenation of its members'''
    def __str__(self):
        return ''.join([str(i) for i in self])

def buildSPO(g):
    spo = {}
    for s, p, o in g:
        p = safePath(g,p)

        if s not in spo:
            spo[s] = {'__self__': s}

        if p in spo[s]:
            spo[s][p].add(o)
        else:
            spo[s][p] = GhostSet({o})

    return spo

def publish(g):
    md = markdown.Markdown()
    spo = buildSPO(g)
    for s in spo:
        tyy = spo[s][safePath(g,RDF.type)]
        for ty in tyy:
            try:
                sty = safePath(g, ty)
                dest = s.replace(CC[''],"pub/"+sty+"/")
                with open(os.path.join('templates',sty),'r') as f:
                    t = jinja2.Template(f.read())
            except IOError as e:
                logging.debug("No template for %s: %s" % (sty, e))
                continue

            logging.warn('Building %s' % dest)

            for r in spo[s].get(safePath(g,CC.rendition),{}):
                mt = spo[r].get(safePath(g,CC.mediaType),{})
                enc = str(spo[r].get(safePath(g,CC.encoding),{'utf-8'}).pop()) # only one encoding

                if rdflib.Literal('text/markdown') in mt:
                    spo[s]['__html__'] = md.convert(IPFS_CLIENT.cat(r))
                    content = t.render(spo[s])
                elif rdflib.Literal('text/html') in mt:
                    content = IPFS_CLIENT.cat(r).decode(enc)
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
    for path, dirs, files in os.walk(TTL_BASE):
      for f in files:
          if f.endswith('.ttl'):
              print("Loading %s from %s" % (f,path))
              g.load(os.path.join(path,f), format='ttl')
    publish(buildGraph(g))
