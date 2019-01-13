#!/usr/bin/python3

import rdflib, re, os, logging, types

from rdflib.namespace import RDF, OWL, SKOS

class RequiredAttributeError(AttributeError):
    pass

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

    def pick(self):
        '''Return one of the items in the set, don't care which.'''
        for i in self:
            return i

    def require(self, a):
        '''Return a TemplatableSet for an attribute and require a non-null response.'''
        s = self.get(a)

        if not s:
            logging.debug('%s: no set items had required property %s' % (repr(self),a))
            raise RequiredAttributeError('no set items had required property %s' % a)

        return s

    def embed(self):
        # syntactic sugar for the benefit of templates
        return TemplatableSet([x.embed() for x in self])

    def teaser(self):
        # syntactic sugar for the benefit of templates
        return TemplatableSet([x.teaser() for x in self])

    def debug(self, e=None):
        if e is None:
            e = self
        elif isinstance(e, str):
            r = "((Debug str %s))" % e
        elif not isinstance(e, TemplatableSet):
            return e.debug()
        r = "((Debug set %s))" % (repr(e))
        logging.info(r)
        return r.replace('&','&amp;').replace('<','&lt;')

    def get(self, a):
        '''Return a TemplatableSet for an attribute, empty or otherwise.'''
        # This is used inside __getattr__ so we cannot raise AttributeError
        # (it will get mysteriously swallowed, even if unrelated to self)
        s = TemplatableSet()
        for i in self:
            if hasattr(i, a):
                g = getattr(i, a)
                if isinstance(g, set):
                    s.update(g)
                elif isinstance(g, str):
                    s.add(g)
                else:
                    raise ValueError('Attribute %s of set element %s was %s, not a set or string' % (a, repr(i), repr(g)))
            else:
                pass # this setelement didn't have the requested attribute :shrug:

        return s

    def count(self, a):
        if isinstance(a, str):
            a = rdflib.URIRef(a)
        c=0
        for i in self:
            if i == a:
                c+=1
        return c

    def is_only(self, a):
        c = self.count(a)
        if c==0:
            return False
        elif c==1:
            return True
        raise rdflib.UniquenessError

    def __getattr__(self, a):
        if a=="__html__":
            raise AttributeError # jinja2 tries this before escaping

        s = self.get(a)
        if not s:
            logging.debug('%s: no set items had required property %s' % (repr(self),a))
            raise AttributeError(a)
        return s

class TemplatableEntity:
    def __init__(self, s, safe):
        if isinstance(s, rdflib.BNode):
            # this is a bit nasty, but
            # in order to reference blank nodes in internally-generated src attributes,
            # we need them to live in a URI scheme
            self.id = rdflib.URIRef("_:"+str(s))
            logging.debug("Protected blank node %s" % self.id)
        elif isinstance(s, rdflib.Literal):
            self.id = rdflib.URIRef("__:"+hash(s))
            self.asEmbed = s # force string value
            logging.debug("Protected literal %s" % self.id)
        else:
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

    def __contains__(self, predicate):
        return predicate in self.po

    def debug(self, e=None):
        if e is None:
            e = self
        elif isinstance(e, str):
            r = "((Debug str %s))" % repr(e)
        elif not isinstance(e, TemplatableEntity):
            return e.debug()
        pp = [("%s = %s" % (repr(k), repr(v))) for k,v in e.po.items()]
        r = "((Debug entity %s\n%s))" % (repr(e), '\n'.join(pp))
        logging.info(r)
        return r.replace('&','&amp;').replace('<','&lt;')

    def teaser(self):
        # syntactic sugar for the benefit of templates
        return '<false-content alt="included from teaser()" context="http://id.colourcountry.net/false/teaser" src="%s"> ' % self.id

    def embed(self):
        # syntactic sugar for the benefit of templates
        return '<false-content alt="included from embed()" context="http://id.colourcountry.net/false/embed" src="%s"> ' % self.id

    def walk(self, p):
        r = TemplatableSet()
        if p not in self.po:
            return r

        for o in self.po[p]:
            r.add(o)
            r.update(o.walk(p))

        return r

    def render(self, template):
        return template.render(self.po)

    def type(self):
        directTypes = self.get('rdf_type')
        parentTypes = TemplatableSet()
        for t in directTypes:
            parentTypes.update(t.walk('rdfs_subClassOf'))
        return directTypes.difference(parentTypes)

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
            raise ValueError("Object must be TemplatableEntity or Literal, not %s %s" % (o.__class__.__name__, repr(o)))
        if not isinstance(p, TemplatablePredicate):
            raise ValueError("Predicate must be TemplatablePredicate, not %s %s" % (p.__class__.__name__, repr(p)))

        if p.safe not in self.po:
            self.po[p.safe] = TemplatableSet()
        self.po[p.safe].add(o)

        if not isinstance(o, rdflib.Literal):
            if o.safe not in self.op:
                self.op[o.safe] = TemplatableSet()
            self.op[o.safe].add(p)

    def get(self, a):
        try:
            return self.po[a]
        except KeyError:
            logging.debug("%s: didn't have property %s" % (repr(self),a))
            return TemplatableSet()

    def __getattr__(self, a):
        try:
            return self.po[a]
        except KeyError:
            logging.debug("%s: didn't have property %s" % (repr(self),a))
            raise AttributeError(a)

    def require(self, a):
        try:
            return getattr(self, a)
        except AttributeError:
            raise RequiredAttributeError('missing required property <%s>.%s' % (self.id,a))

    def __str__(self):
        if 'rdf_type' in self:
            return str(self.require('asEmbed'))
        else:
            return self.id # not a concept, just a URI

    def __repr__(self):
        return "<Entity %s %s (%s)>" % (self.id, id(self), self.safe)

class TemplatablePredicate(TemplatableEntity):
    def __init__(self, p, safe):
        TemplatableEntity.__init__(self, p, safe)
        self.so = {}

    def __str__(self):
        return self.id

    def __repr__(self):
        return "<Predicate %s %s (%s)>" % (self.id, hash(self), self.safe)

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
                    p = p[len(n):]
                    break
                else:
                    p = px+'_'+p[len(n):]
                    break
        else:
            if p.startswith("_:"): # blank nodes
                p = p[2:]

        # FIXME: prefixes and protocols can collide
        return re.sub('[^A-Za-z0-9-]','_',p)

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

    def wipe(self, s, p):
        ss, sp = self.safePath(s), self.safePath(p)
        if ss not in self.entities:
            raise ValueError("%s: entity does not exist, can't wipe %s" % (s, p))
        if sp not in self.predicates:
            raise ValueError("%s: predicate does not exist, can't wipe %s" % (s, p))

        tes = self.entities[ss]
        tep = self.predicates[sp]
        teip = self.inv_predicates[sp]

        for teo in tes.po[sp]:
            if not isinstance(teo, rdflib.Literal):
                # FIXME: haven't tested this
                tes.op[teo.safe].remove(tep)
                teo.op[tes.safe].remove(teip)
                del(teo.po[teip.safe])
                del(teip.so[teo.safe])

        del(tes.po[sp])
        del(tep.so[ss])

    def add(self, s, p, o):
        ss, sp = self.safePath(s), self.safePath(p)
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
            return

        so = self.safePath(o)
        if so not in self.entities:
            self.entities[so] = TemplatableEntity(o, so)
        teo = self.entities[so]

        teo.add(teip, tes)
        tes.add(tep, teo)
        tep.addso(tes, teo)
        teip.addso(teo, tes)
