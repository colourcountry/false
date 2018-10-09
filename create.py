#!/usr/bin/python3

import rdflib
from rdflib.namespace import RDF, DC, SKOS, OWL
import sys, logging, os, uuid, re
import ipfsapi
import jinja2, markdown
import pprint

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
                o = open(os.path.join(TTL_BASE,fn),'r').read()
                blob_id = addRendition(gg, DOCS[s], o.encode('utf-8'),
                    mediaType=rdflib.Literal(mime),
                    charset=rdflib.Literal('utf-8')
                    )
            except IOError:
                logging.warn("Couldn't open %s" % fn)
    return gg

class TemplatableSet(set):
    '''This set can be referenced in templates.
    If there are multiple items in the set they are concatenated.
    If the items of the set are all sets, each attribute of the set is the union of that attribute of all its items.
    The rationale is that by traversing the graph of entities you will end up at a set of sets of literals,
    which will be the thing you want to display.'''
    
    def __str__(self):
        return ''.join(str(i) for i in self)

    def surround(self, pfx, sfx, ifx=''):
        return pfx+(sfx+ifx+pfx).join(str(i) for i in self)+sfx

    def __getattr__(self, a):
        s = TemplatableSet()
        for i in self:
            g = getattr(i, a)
            if not isinstance(g, set):
                raise AttributeError('Attribute %s of element %s was not a set' % (a, i))
            s.update(g)
        return s

class TemplatableEntity:
    def __init__(self, s, safe):
        self.id = s
        self.safe = safe
        self.po = {}
        self.op = {}

    def __hash__(self):
        return id(self)

    def __eq__(self):
        return self.safe == other.safe

    def add(self, p, o):
        if p not in self.po:
            self.po[p] = TemplatableSet()
        self.po[p].add(o)
        if o not in self.op:
            self.op[o] = TemplatableSet()
        self.op[o].add(p)

    def __getattr__(self, a):
        if a in self.po:
            return self.po[a]
        return set()

    def __str__(self):
        return self.id

    def __repr__(self):
        r = "<Entity %s (%s) :=\n" % (self.id, self.safe)
        for p in self.po:
            r += " + %s %s\n" % (p, ','.join(str(x) for x in self.po[p]))
        return r+">\n"

class TemplatablePredicate:
    def __init__(self, p, safe):
        self.id = p
        self.safe = safe
        self.so = {}

    def __str__(self):
        return self.id

    def __repr__(self):
        return "<Predicate %s (%s)>" % (self.id, self.safe)

    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self.safe == other.safe

    def add(self, s, o):
        if s not in self.so:
            self.so[s] = TemplatableSet()
        self.so[s].add(o)

class TemplatableGraph:
    def __init__(self, g=None):
        if g is None:
            self.g = Graph()
        else:
            self.g = g

        self.entities = {}
        self.predicates = {}
        self.inv_predicates = {}

        for s, p, o in g:
            self.add(s, p, o)

    def safePath(self, p):
        for (px, n) in self.g.namespaces():
            if p.startswith(n):
                return px+'_'+p[len(n):]
        return "lit_"+re.sub('[^A-Za-z0-9]','_',p)

    def __contains__(self, e):
        return (self.safePath(e) in self.entities)

    def __getattr__(self, a):
        if a in self.entities:
            return self.entities[a]
        raise AttributeError(a)

    def add(self, s, p, o):
        ss, sp, so = self.safePath(s), self.safePath(p), self.safePath(o)
        if ss not in self.entities:
            self.entities[ss] = TemplatableEntity(s, ss)
        s = self.entities[ss]
        if sp not in self.predicates:
            self.predicates[sp] = TemplatablePredicate(p, sp)
        p = self.predicates[sp]
        if sp not in self.inv_predicates:
            tempinv = TemplatablePredicate('inv_'+p.id, 'inv_'+p.safe)
            self.inv_predicates[sp] = tempinv
            self.inv_predicates[tempinv.safe] = p

        if isinstance(o,rdflib.Literal):
            s.add(sp, o)
        else:
            if so not in self.entities:
                self.entities[so] = TemplatableEntity(o, so)
            o = self.entities[so]
            o.add(self.inv_predicates[sp].safe,s)
            s.add(sp, o)




def publish(g):
    md = markdown.Markdown()
    tg = TemplatableGraph(g)

    for e in tg.entities:
        for eType in tg.entities[e].rdf_type:
            try:
                dest = os.path.join("pub",eType.safe,e)
                with open(os.path.join('templates',eType.safe),'r') as f:
                    t = jinja2.Template(f.read())
            except IOError as err:
                logging.debug("No template for %s: %s" % (eType.safe, err))
                continue

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
    for path, dirs, files in os.walk(TTL_BASE):
      for f in files:
          if f.endswith('.ttl'):
              print("Loading %s from %s" % (f,path))
              g.load(os.path.join(path,f), format='ttl')
    publish(buildGraph(g))
