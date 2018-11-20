#!/usr/bin/python3

import rdflib, re, os, logging

from rdflib.namespace import RDF, OWL, SKOS

class TemplatableSet(set):
    '''This set can be referenced in templates.
    If there are multiple items in the set they are concatenated.
    If the items of the set are all sets, each attribute of the set is the union of that attribute of all its items.
    The rationale is that by traversing the graph of entities you will end up at a set of sets of literals,
    which will be the thing you want to display.'''

    def __str__(self):
        return ''.join(str(i) for i in self)

    def __repr__(self):
        return '<TS:'+','.join(repr(i) for i in self)+'>'

    def surround(self, pfx, sfx, ifx=''):
        return pfx+(sfx+ifx+pfx).join(str(i) for i in self)+sfx

    def join(self, ifx):
        return self.surround('', '', ifx)

    def difference(self, other):
        return TemplatableSet(set.difference(self, other))

    def __getattr__(self, a):
        if a=="__html__":
            raise AttributeError # jinja2 tries this before escaping

        s = TemplatableSet()
        for i in self:
            # Any AttributeError raised inside __getattr__ will get mysteriously swallowed,
            # even if unrelated to self, so check first
            if hasattr(i, a):
                g = getattr(i, a)
                if isinstance(g, set):
                    s.update(g)
                else:
                    raise ValueError('Attribute %s of set element %s was %s, not a set' % (a, repr(i), repr(g)))
            else:
                raise ValueError('Set element %s did not have attribute %s' % (repr(i), a))
        return s

class TemplatableEntity:
    def __init__(self, s, safe):
        self.id = s
        self.safe = safe
        self.po = {'this': self}
        self.op = {}

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        # note: this makes equality non-symmetrical as it means
        # TemplatableEntity can == other types, but not the other way around.
        return self.__hash__() == other.__hash__()

    def debug(self):
        return repr(self).replace('&','&amp;').replace('<','&lt;')

    def walk(self, p):
        r = TemplatableSet()
        if p not in self.po:
            return r

        for o in self.po[p]:
            r.add(o)
            r.update(o.walk(p))

        return r

    def type(self):
        parentTypes = TemplatableSet()
        for t in self.rdf_type:
            parentTypes.update(t.walk('rdfs_subClassOf'))
        return self.rdf_type.difference(parentTypes)

    def rels(self, o):
        return TemplatableSet(p for p in self.op.get(o.safe, []))

    def rel(self, o):
        leaves = self.rels(o)
        for p in self.op.get(o.safe, []):
            parents = p.walk('rdfs_subPropertyOf')
            for parent in parents:
                leaves.discard(parent)
        return leaves

    def add(self, p, o):
        if not (isinstance(o, TemplatableEntity) or isinstance(o, rdflib.Literal)):
            raise ValueError("Object must be TemplatableEntity or Literal, not %s %s" % (o.__class__.__name__, o))
        if not isinstance(p, TemplatablePredicate):
            raise ValueError("Predicate must be TemplatablePredicate, not %s %s" % (p.__class__.__name__, p))

        if p.safe not in self.po:
            self.po[p.safe] = TemplatableSet()
        self.po[p.safe].add(o)

        if not isinstance(o, rdflib.Literal):
            if o.safe not in self.op:
                self.op[o.safe] = TemplatableSet()
            self.op[o.safe].add(p)

    def __getattr__(self, a):
        if a in self.po:
            return self.po[a]
        return TemplatableSet()

    def __str__(self):
        return self.id

    def __repr__(self):
        r = "<Entity %s %s (%s) :=\n" % (self.id, hash(self), self.safe)
        for p in self.po:
            if isinstance(self.po[p], set):
                j = ','.join(str(x) for x in self.po[p])
            else:
                j = repr(self.po[p].id)
            r += " + %s %s\n" % (p, j)
        return r+">\n"

class TemplatablePredicate(TemplatableEntity):
    def __init__(self, p, safe):
        TemplatableEntity.__init__(self, p, safe)
        self.so = {}

    def __str__(self):
        return self.id

    def __repr__(self):
        r = "<Predicate %s %s (%s) :=\n" % (self.id, hash(self), self.safe)
        for p in self.po:
            if isinstance(self.po[p], set):
                j = ','.join(str(x) for x in self.po[p])
            else:
                j = repr(self.po[p].id)
            r += " + %s %s\n" % (p, j)
        return r+">\n"

    def addso(self, s, o):
        if not (isinstance(o, TemplatableEntity) or isinstance(o, rdflib.Literal)):
            raise ValueError("Must add TemplatableEntity or Literal to graph, not %s %s" % (o.__class__.__name__, o))
        if not (isinstance(s, TemplatableEntity) or isinstance(s, rdflib.Literal)):
            raise ValueError("Must add TemplatableEntity or Literal to graph, not %s %s" % (s.__class__.__name__, s))

        if s.safe not in self.so:
            self.so[s.safe] = TemplatableSet()
        self.so[s.safe].add(o)

class TemplatableGraph:
    def __init__(self, g=None):
        if g is None:
            self.g = Graph()
        else:
            self.g = g
            logging.debug("Found namespaces: %s" % '\n'.join([str(x) for x in self.g.namespaces()]))

        self.entities = {}
        self.predicates = {}
        self.inv_predicates = {}

        # Get predicate information we'll need to build the graph

        # this one is an axiom
        self.addPredicate(OWL['inverseOf'], OWL['inverseOf'])

        for s, p, o in g:
            # look for statements about predicates
            if p == RDF['type']:
                if o == OWL['SymmetricProperty']:
                    self.addPredicate(s, s)
                elif o == OWL['ObjectProperty'] or o == OWL['DatatypeProperty']:
                    self.addPredicate(s)
            elif p == OWL['inverseOf']:
                self.addPredicate(s, o)

        for s, p, o in g:
            self.addPredicate(p)

        self.addInversePredicates()

        # Add in the actual graph
        for s, p, o in g:
            self.add(s, p, o)

        inferredTriples = []

        # Add inferred predicates
        for s, p, o in g:
            sp = self.safePath(p)

            if sp in self.entities:
                supers = self.entities[sp].walk('rdfs_subPropertyOf')
                for pp in supers:
                    inferredTriples.append((s, pp.id, o))

        # Add inferred types
        for s in self.entities.values():
            for t in s.po.get('rdf_type',[]):
                for tt in self.entities[t.safe].walk('rdfs_subClassOf'):
                    inferredTriples.append((s.id, self.predicates['rdf_type'].id, tt.id))

        for s, p, o in inferredTriples:
            self.add(s, p, o)

    def safePath(self, p):
        for (px, n) in self.g.namespaces():
            if p.startswith(n):
                if px=='':
                    return p[len(n):]
                else:
                    return px+'_'+p[len(n):]
        # NOTE: prefixes and protocols can collide
        return re.sub('[^A-Za-z0-9]','_',p) 

    def __contains__(self, e):
        return (e in self.entities)

    def __getattr__(self, a):
        if a in self.entities:
            return self.entities[a]
        raise AttributeError(a)

    def addPredicate(self, p, ip=None):
        sp = self.safePath(p)

        if sp in self.predicates:
            if ip is None:
                return # already know about it
            if self.predicates[sp].id == p and self.inv_predicates.get(sp, None) == ip:
                logging.debug("Duplicate predicate definition for %s" % p)
                return
            if self.predicates[sp].id == ip and self.inv_predicates.get(sp, None) == p:
                logging.debug("Duplicate predicate definition for %s (inverse)" % p)
                return

            logging.debug("Re-registering predicate %s with inverse %s (was %s)" % (p, ip, self.inv_predicates.get(sp, None)))
        else:
            self.predicates[sp] = TemplatablePredicate(p, sp)
            self.entities[sp] = self.predicates[sp]

            logging.debug("Registering predicate %s with inverse %s" % (p, ip))

        if ip is None:
            return

        sip = self.safePath(ip)
        self.inv_predicates[sip] = self.predicates[sp]

        if p != ip:
            self.predicates[sip] = TemplatablePredicate(ip, sip)
            self.entities[sip] = self.predicates[sip]
            self.inv_predicates[sp] = self.predicates[sip]

    def addInversePredicates(self):
        for sp, p in list(self.predicates.items()):
            if sp not in self.inv_predicates:
                self.addPredicate(p.id, 'inv_'+p.id)

    def add(self, s, p, o):
        ss, sp, so = self.safePath(s), self.safePath(p), self.safePath(o)
        if ss not in self.entities:
            self.entities[ss] = TemplatableEntity(s, ss)
        tes = self.entities[ss]

        if sp not in self.predicates:
            self.predicates[sp] = TemplatablePredicate(p, sp)
        tep = self.predicates[sp]
        teip = self.inv_predicates[sp]

        if isinstance(o,rdflib.Literal):
            tes.add(tep, o)
            tep.addso(tes, o)
        else:
            if so not in self.entities:
                self.entities[so] = TemplatableEntity(o, so)
            teo = self.entities[so]

            teo.add(teip, tes)
            tes.add(tep, teo)
            tep.addso(tes, teo)
            teip.addso(teo, tes)
