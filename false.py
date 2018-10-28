#!/usr/bin/python3

import rdflib
from rdflib.namespace import RDF, DC, SKOS, OWL
import sys, logging, os, uuid, re, urllib.parse, datetime
import ipfsapi
import jinja2, markdown
import pprint
from triplate import *

logging.basicConfig(level=logging.DEBUG)

FALSE_SRC = os.environ["FALSE_SRC"]
FALSE_URL_BASE = os.environ["FALSE_URL_BASE"]
FALSE_OUT = os.environ["FALSE_OUT"]
FALSE_TEMPLATES = os.environ["FALSE_TEMPLATES"]

class MockIPFS:
    def __init__(self, file_base, url_base):
        self.base = os.path.join(file_base, 'blob')
        self.namespace = rdflib.Namespace("%s/%s/" % (url_base, 'blob'))
        os.makedirs(self.base, exist_ok=True)

    def add_bytes(self, blob):
        r = str(uuid.uuid4())
        logging.debug("Writing blob %s" % r)
        with open(os.path.join(self.base, r), 'wb') as f:
            f.write(blob)
        return r

    def cat(self, r):
        logging.debug("Reading blob %s" % r)
        with open(os.path.join(self.base, r[-36:]), 'rb') as f:
            return f.read()


try:
    IPFS_CLIENT = ipfsapi.connect('127.0.0.1',5001)
    IPFS = rdflib.Namespace("/ipfs/")
except ipfsapi.exceptions.ConnectionError:
    logging.warn("No IPFS daemon running.")
    IPFS_CLIENT = MockIPFS(FALSE_OUT, FALSE_URL_BASE)
    IPFS = IPFS_CLIENT.namespace

FILE_TYPES = { ".html": "text/html", ".txt": "text/plain", ".md": "text/markdown" }
F = rdflib.Namespace("http://www.colourcountry.net/false/model/")


def addRendition(g, doc_id, blob, **properties):
    if not IPFS_CLIENT:
        logging.warn("No IPFS, can't add rendition info to document %s" % doc_id)
        return None

    r = IPFS_CLIENT.add_bytes(blob)
    blob_id = IPFS[r]
    g.add((blob_id, RDF.type, F.Media))
    for k, v in properties.items():
        g.add((blob_id, F[k], v))
    g.add((doc_id, F.rendition, blob_id))
    return blob_id


def buildGraph(g):
    gg = rdflib.Graph()
    gg.bind('f', F)
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
            if o == F['Document']:
                ENTS[s] = F[os.path.basename(s)]
                DOCS[s] = ENTS[s]

    for s, p, o in g:
        if o in ENTS:
            o = ENTS[o]

        if p == F['markdown']:
            blob_id = addRendition(gg, DOCS[s], o.encode('utf-8'),
                mediaType=rdflib.Literal('text/markdown'),
                charset=rdflib.Literal('utf-8')
            )
        else:
            gg.add((ENTS[s], p, o))

    for s, doc_id in DOCS.items():
        for t, mime in FILE_TYPES.items():
            fn = os.path.basename(s+t)
            try:
                o = open(os.path.join(FALSE_SRC,fn),'rb').read()
                blob_id = addRendition(gg, DOCS[s], o,
                    mediaType=rdflib.Literal(mime),
                    charset=rdflib.Literal('utf-8') # let's hope
                    )
            except IOError:
                logging.warn("Couldn't open %s" % fn)
    return gg

def getDestPath(e, e_type, file_type='html'):
    return os.path.join(FALSE_OUT, e_type.safe, e+'.'+file_type)

def getDestURL(e, e_type, file_type='html'):
    return '%s/%s/%s' % (FALSE_URL_BASE, e_type.safe, e+'.'+file_type)



def publish(g):
    md = markdown.Markdown()
    tg = TemplatableGraph(g)

    stage = {}

    for e in tg.entities:

        # use the most direct type because we need to go up in a specific order
        # FIXME: provide ordered walk functions on entities?
        e_types = tg.entities[e].type()
        e_id = tg.entities[e].id

        while e_types:
            for e_type in e_types:
                try:
                    with open(os.path.join(FALSE_TEMPLATES,e_type.safe),'r') as f:
                        t = jinja2.Template(f.read())
                except IOError as err:
                    logging.debug("%s: no template %s" % (e, e_type.safe))
                    continue

                dest = getDestPath(e, e_type)
                url = getDestURL(e, e_type)

                logging.info('%s: will render as %s -> %s' % (e, e_type, dest))
                stage[dest] = (t, e)
                e_types = None # found a renderable type

                # add triples for the template to pick up
                tg.add(e_id, F.url, rdflib.Literal(url))

                tg.add(e_id, F.published, rdflib.Literal(datetime.datetime.now().isoformat()))

                for r in tg.entities[e].f_rendition:
                    mt = r.f_mediaType
                    enc = r.f_charset.pop()
                    logging.info('%s: found %s rendition' % (e, mt))

                    if rdflib.Literal('text/markdown') in mt:
                        tg.add(e_id, F.html, rdflib.Literal(md.convert(IPFS_CLIENT.cat(r.id).decode(enc))))
                    elif rdflib.Literal('text/html') in mt:
                        tg.add(e_id, F.html, rdflib.Literal(IPFS_CLIENT.cat(r.id).decode(enc)))
                    else:
                        raise RuntimeError("No renderable media type for %s (%s)" % (e, mt))

                break


            if e_types is not None:
                # get the next layer of types
                e_types = e_types.rdfs_subClassOf


    logging.info("Stage is ready")

    for dest in stage:
        t, e = stage[dest]
        content = t.render(tg.entities[e].po)

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        logging.info("%s: writing %s" % (e, dest))
        with open(dest,'w') as f:
            f.write(content)


if __name__=="__main__":
    g = rdflib.Graph()
    for path, dirs, files in os.walk(FALSE_SRC):
      for f in files:
          if f.endswith('.ttl'):
              logging.info("Loading %s from %s" % (f,path))
              g.load(os.path.join(path,f), format='ttl')
    publish(buildGraph(g))
