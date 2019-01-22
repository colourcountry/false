#!/usr/bin/python3

import rdflib
from rdflib.namespace import RDF, RDFS, DC, SKOS, OWL, XSD
import logging, os, re, io, datetime, markdown, urllib.parse, json, posixpath

F = rdflib.Namespace("http://id.colourcountry.net/false/")

CONTENT_NEW = 0
CONTENT_READY = 1
CONTENT_EXTERNAL = 2

class ValidationError(ValueError):
    pass

# h/t https://stackoverflow.com/questions/29259912/how-can-i-get-a-list-of-image-urls-from-a-markdown-file-in-python
class ImgExtractor(markdown.treeprocessors.Treeprocessor):
    def __init__(self, md, base):
        self.base = base
        super(ImgExtractor, self).__init__(md)

    def run(self, doc):
        "Find all images and links and create lists of them. "
        self.markdown.images = []
        self.markdown.links = []

        # <false-content> tags are embeds if their contexts is the special embed context
        # otherwise we assume they are links
        for el in doc.findall('.//false-content'):
            if rdflib.URIRef(el.get('context')) == F.embed:
                self.markdown.images.append(urllib.parse.urljoin(self.base, el.get('src')))
            else:
                self.markdown.links.append(urllib.parse.urljoin(self.base, el.get('src')))

        # All of these will get transformed into <false-content> in the publish stage
        for el in doc.findall('.//img'):
            self.markdown.images.append(urllib.parse.urljoin(self.base, el.get('src')))
        for el in doc.findall('.//false-embed'):
            self.markdown.images.append(urllib.parse.urljoin(self.base, el.get('src')))
        for el in doc.findall('.//false-teaser'):
            self.markdown.links.append(urllib.parse.urljoin(self.base, el.get('src')))

        # Regular links (which are safe because they don't use any data from the linked item)
        for el in doc.findall('.//a'):
            self.markdown.links.append(urllib.parse.urljoin(self.base, el.get('href')))


class ImgExtExtension(markdown.extensions.Extension):
    def __init__(self, **kwargs):
        self.config = {'base' : ['http://example.org/', 'The base URI to use when embedded content is specified as a relative URL']}
        super(ImgExtExtension, self).__init__(**kwargs)

    def extendMarkdown(self, md, md_globals):
        img_ext = ImgExtractor(md, self.getConfig('base'))
        md.treeprocessors.add('imgext', img_ext, '>inline')

class Builder:
    def __init__(self, g, cfg, files, file_types):
        self.cfg = cfg
        self.g = g
        self.files = files
        self.file_types = file_types

        # Content can appear in these contexts.
        self.contexts_for_rights = {
                    F.public: {F.link, F.teaser, F.embed, F.page, F.download},
                    # Restricted content may still get a page at publish time, but it must not contain the content
                    F.restricted: {F.link, F.teaser}
        }


    def add_rendition(self, mediaType, blob=None, blob_hash=None, content_id=None, **properties):
        if not self.cfg.ipfs_client:
            logging.warning("No IPFS, can't add rendition info to document %s" % content_id)
            return None

        if blob_hash:
            logging.debug("%s: using provided IPFS hash %s" % (content_id, blob_hash))
        else:
            blob_hash = self.cfg.ipfs_client.add_bytes(blob)

        doc_basename = posixpath.basename(content_id)
        if doc_basename:
            blob_filename = doc_basename+self.file_types[str(mediaType)]
        else:
            blob_filename = "blob"+self.file_types[str(mediaType)]

        # TODO: spot blank nodes and remove the filename (since it's not guaranteed to be stable)

        info = rdflib.Graph()
        info.bind('', F)
        for p, o in self.g[content_id]:
            if p != F.rendition: # we might know about other renditions already, but it's pot luck, so best to keep just this one
                info.add((content_id, p, o))
        info.add((content_id, F.rendition, rdflib.URIRef(blob_filename))) # relative path to the file, as we don't know the hash
        info_blob = info.serialize(format='ttl')

        ipld = {"Links": [{"Name": blob_filename, "Hash": blob_hash}], # FIXME "Size": len(blob)}],
                "Data": "\u0008\u0001"} # this data seems to be required for something to be a directory

        if info_blob:
            info_hash = self.cfg.ipfs_client.add_bytes(info_blob)
            ipld["Links"].append({"Name": "info.ttl", "Hash": info_hash, "Size": len(info_blob)})

        ipld_blob = json.dumps(ipld).encode('utf-8')
        wrapper_resp = self.cfg.ipfs_client.object_put(io.BytesIO(ipld_blob))

        wrapped_id = self.cfg.ipfs_namespace["%s/%s" % (wrapper_resp["Hash"], blob_filename)]
        self.g.add((wrapped_id, RDF.type, F.Media))
        self.g.add((wrapped_id, F.mediaType, mediaType))
        self.g.add((wrapped_id, F.blobURL, wrapped_id))

        for k, v in properties.items():
            logging.debug("%s: adding property %s=%s" % (content_id, k, v))
            self.g.add((wrapped_id, F[k], v))
        logging.debug("%s: adding rendition %s" % (content_id, wrapped_id))
        self.g.add((content_id, F.rendition, wrapped_id))
        return wrapped_id

    def add_markdown_refs(self, content_id, blob):
        html = self.mdproc.convert(blob)
        for url in self.mdproc.images:
            uriref = rdflib.URIRef(url)
            if uriref in self.private_ids:
                logging.debug("%s: found embed of private entity %s, doing nothing" % (content_id, url))
            elif uriref in self.content:
                logging.debug("%s: found embed of %s" % (content_id, url))
                self.g.add((content_id, F.incorporates, uriref))
            else:
                # an "embed" of a non-content is just a long-winded mention
                self.g.add((content_id, F.mentions, uriref))

        for url in self.mdproc.links:
            uriref = rdflib.URIRef(url)
            if uriref in self.private_ids:
                logging.info("%s: found mention of private entity %s, doing nothing" % (content_id, url))
            else:
                logging.info("%s: found mention of %s" % (content_id, url))
                self.g.add((content_id, F.mentions, uriref))

    def build(self):
        self.g.bind('ipfs', self.cfg.ipfs_namespace)

        # first excise all private stuff, we don't want to know about it
        self.private_ids = set()

        logging.debug( list(self.g.triples((None, F.hasPublicationRights, F.private))) )
        for triple in self.g.triples((None, F.hasPublicationRights, F.private)):
            entity_id = triple[0]
            logging.info("%s: is private, dropping" % entity_id)
            self.private_ids.add(entity_id)
            self.g.remove((entity_id, None, None))
            self.g.remove((None, entity_id, None))
            self.g.remove((None, None, entity_id))


        self.content = {}
        self.valid_contexts = {}

        self.mdproc = markdown.Markdown(extensions=[ImgExtExtension(base=self.cfg.id_base), 'tables'])
        doc_types = [x[0] for x in self.g.query("""select ?t where { ?t rdfs:subClassOf+ :Content }""")]


        type_spo = self.g.triples((None, RDF.type, None))
        for s, p, o in type_spo:
            if o == F.Content or o in doc_types:
                logging.debug("Found content %s (%s)" % (s, s.__class__.__name__))
                self.content[s] = CONTENT_NEW

        rights_spo = self.g.triples((None, F.hasPublicationRights, None))
        for s, p, o in rights_spo:
            if s in self.valid_contexts:
                raise ValueError("%s: non-content or multiple publication rights" % s)
            self.valid_contexts[s] = self.contexts_for_rights[o]

        renditions_to_add = []

        for content_id in self.content:
            self.g.add((content_id, F.published, rdflib.Literal(datetime.datetime.now().isoformat(), datatype=XSD.dateTime)))

            # find rights info
            if content_id not in self.valid_contexts:
                logging.debug("%s: no publication rights specified, defaulting to public" % content_id)
                self.valid_contexts[content_id] = self.contexts_for_rights[F.public]
                self.g.add((content_id, F.hasPublicationRights, F.public))

            # look for markdown
            md = self.g.triples((content_id, F.markdown, None))
            for spo in md:
                s, p, o = spo

                if F.page not in self.valid_contexts[content_id]:
                    logging.debug("%s: discarding markdown because page is not a valid context" % content_id)
                else:
                    logging.debug("%s: adding rendition for markdown property:\n   %s..." % (content_id, o[:100].strip()))
                    renditions_to_add.append({
                        'content_id': content_id,
                        'blob': o.encode('utf-8'),
                        'mediaType': rdflib.Literal('text/markdown'),
                        'charset': rdflib.Literal('utf-8'),
                        'intendedUse': F.page # embeds will fall back to this, but it can be distinguished from stuff to offer as a download
                    })

                    self.content[s] = CONTENT_READY

                    self.add_markdown_refs(s, o)


        for ctx, id_to_file in self.files.items():
            for content_id, fn in id_to_file.items():
                if content_id not in self.valid_contexts or ctx not in self.valid_contexts[content_id]:
                    continue
                for mime, ext in self.file_types.items():
                    if not fn.endswith(ext):
                        continue ## FIXME this is a silly way around

                    m = re.match('.*[.]ipfs-([^.]+)[.].*', fn)
                    if m:
                        blob = None
                        blob_hash = m.group(1)
                        renditions_to_add.append({
                            'content_id': content_id,
                            'blob_hash': blob_hash,
                            'mediaType': rdflib.Literal(mime),
                            'charset': rdflib.Literal("utf-8"),
                            'intendedUse': ctx
                        })
                    else:
                        blob = open(fn,'rb').read()
                        renditions_to_add.append({
                            'content_id': content_id,
                            'blob': blob,
                            'mediaType': rdflib.Literal(mime),
                            'charset': rdflib.Literal("utf-8"),
                            'intendedUse': ctx
                        })

                    logging.info("%s@@%s: using %s" % (content_id, ctx, fn))

                    if content_id in self.content:
                        # we've got something, even if it may be intended for a different context
                        # TODO: figure out if this is a bad idea
                        self.content[content_id] = CONTENT_READY

                    if mime == 'text/markdown':
                        if not blob:
                            blob = open(fn,'rb').read()
                        self.add_markdown_refs(content_id, blob.decode('utf-8'))

        # markdown properties have all been converted or discarded
        self.g.remove((None, F.markdown, None))

        # graph is now ready
        for r in renditions_to_add:
            blob_id = self.add_rendition(**r)

        return self.g
