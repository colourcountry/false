#!/usr/bin/python3

import rdflib, re, os, urllib.parse, logging

from rdflib.namespace import RDF, OWL
CC = rdflib.Namespace("http://www.colourcountry.net/thing/")

# FIXME
FALSE_URL_BASE = os.environ["FALSE_URL_BASE"]
FALSE_OUT = os.environ["FALSE_OUT"]

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
        return hash(self.id)

    def __eq__(self, other):
        # note: this makes equality non-symmetrical as it means
        # TemplatableEntity can == other types, but not the other way around.
        return self.__hash__() == other.__hash__()

    def add(self, p, o):
        if p not in self.po:
            self.po[p] = TemplatableSet()
        self.po[p].add(o)
        if o not in self.op:
            self.op[o] = TemplatableSet()
        self.op[o].add(p)

    def url(self, eType=None):
        if eType is None:
            if CC['Document'] in self.rdf_type:
                eType = CC['Document']

        for sType in self.rdf_type:
            if sType == eType:
                return urllib.parse.urljoin(FALSE_URL_BASE, "/".join((FALSE_OUT, sType.safe, self.safe)))

        raise TypeError("Entity %s does not have requested type %s" % (self, eType))


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
        return hash(self.id)

    def __eq__(self, other):
        return self.__hash__() == other.__hash__()

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
            if p == RDF['type'] and o == OWL['SymmetricProperty']:
                self.addPredicate(s, s)
            elif p == OWL['inverseOf']:
                self.addPredicate(s, o)

        for s, p, o in g:
            self.add(s, p, o)

    def safePath(self, p):
        for (px, n) in self.g.namespaces():
            if p.startswith(n):
                return px+'_'+p[len(n):]
        return "lit_"+re.sub('[^A-Za-z0-9]','_',p)

    def __contains__(self, e):
        return (e in self.entities)

    def __getattr__(self, a):
        if a in self.entities:
            return self.entities[a]
        raise AttributeError(a)

    def addPredicate(self, p, ip):
        logging.warn("Pre-loading predicate %s with inverse %s" % (p, ip))
        sp, sip = self.safePath(p), self.safePath(ip)
        self.predicates[sp] = TemplatablePredicate(p, sp)
        if p == ip:
            self.predicates[sip] = self.predicates[sp]
        else:
            self.predicates[sip] = TemplatablePredicate(ip, sip)
        self.inv_predicates[sp] = self.predicates[sip]
        self.inv_predicates[sip] = self.predicates[sp]

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
