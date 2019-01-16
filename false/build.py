#!/usr/bin/python3

import rdflib
from rdflib.namespace import RDF, RDFS, DC, SKOS, OWL, XSD
import logging, os, re, io, datetime, markdown, urllib.parse, json, posixpath

F = rdflib.Namespace("http://id.colourcountry.net/false/")


FILE_TYPES = { "text/html": ".html",
               "text/plain": ".txt",
               "text/markdown": ".md",
               "image/jpeg": ".jpg",
               "image/png": ".png",
               "application/pdf": ".pdf" }

# Local content can appear in these contexts
CONTEXTS_FOR_RIGHTS = {
            F.public: {F.link, F.teaser, F.embed, F.page, F.download},
            # Restricted content may still get a page at publish time, but it must not contain the content
            F.restricted: {F.link, F.teaser}
}

# Remote content, or non-content, can only appear as a plain link or teaser
LIMITED_CONTEXTS = {F.link, F.teaser}

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

def add_rendition(g, ipfs_client, ipfs_namespace, mediaType, blob=None, blob_hash=None, content_id=None, **properties):
    if not ipfs_client:
        logging.warning("No IPFS, can't add rendition info to document %s" % content_id)
        return None

    if blob_hash:
        logging.debug("%s: using provided IPFS hash %s" % (content_id, blob_hash))
    else:
        blob_hash = ipfs_client.add_bytes(blob)

    doc_basename = posixpath.basename(content_id)
    if doc_basename:
        blob_filename = doc_basename+FILE_TYPES[str(mediaType)]
    else:
        blob_filename = "blob"+FILE_TYPES[str(mediaType)]

    # TODO: spot blank nodes and remove the filename (since it's not guaranteed to be stable)

    info = rdflib.Graph()
    info.bind('', F)
    for p, o in g[content_id]:
        if p != F.rendition: # we might know about other renditions already, but it's pot luck, so best to keep just this one
            info.add((content_id, p, o))
    info.add((content_id, F.rendition, rdflib.URIRef(blob_filename))) # relative path to the file, as we don't know the hash
    info_blob = info.serialize(format='ttl')

    ipld = {"Links": [{"Name": blob_filename, "Hash": blob_hash}], # FIXME "Size": len(blob)}],
            "Data": "\u0008\u0001"} # this data seems to be required for something to be a directory

    if info_blob:
        info_hash = ipfs_client.add_bytes(info_blob)
        ipld["Links"].append({"Name": "info.ttl", "Hash": info_hash, "Size": len(info_blob)})

    ipld_blob = json.dumps(ipld).encode('utf-8')
    wrapper_resp = ipfs_client.object_put(io.BytesIO(ipld_blob))

    wrapped_id = ipfs_namespace["%s/%s" % (wrapper_resp["Hash"], blob_filename)]
    g.add((wrapped_id, RDF.type, F.Media))
    g.add((wrapped_id, F.mediaType, mediaType))
    g.add((wrapped_id, F.blobURL, wrapped_id))

    for k, v in properties.items():
        logging.debug("%s: adding property %s=%s" % (content_id, k, v))
        g.add((wrapped_id, F[k], v))
    logging.debug("%s: adding rendition %s" % (content_id, wrapped_id))
    g.add((content_id, F.rendition, wrapped_id))
    return wrapped_id

def build_graph(g, cfg, files):
    g.bind('ipfs', cfg.ipfs_namespace)

    # first excise all private stuff, we don't want to know about it
    private_ids = set()
    logging.debug( list(g.triples((None, F.hasPublicationRights, F.private))) )
    for triple in g.triples((None, F.hasPublicationRights, F.private)):
        entity_id = triple[0]
        logging.info("%s: is private, dropping" % entity_id)
        private_ids.add(entity_id)
        g.remove((entity_id, None, None))
        g.remove((None, entity_id, None))
        g.remove((None, None, entity_id))

    doc_types = [x[0] for x in g.query("""select ?t where { ?t rdfs:subClassOf+ :Content }""")]

    content = {}
    valid_contexts = {}

    mdproc = markdown.Markdown(extensions=[ImgExtExtension(base=cfg.id_base)])

    type_spo = g.triples((None, RDF.type, None))
    for s, p, o in type_spo:
        if o == F.Content or o in doc_types:
            logging.debug("Found content %s (%s)" % (s, s.__class__.__name__))
            content[s] = CONTENT_NEW
        else:
            if s.startswith(cfg.id_base):
                # local non-content can be teased
                valid_contexts[s] = LIMITED_CONTEXTS

    rights_spo = g.triples((None, F.hasPublicationRights, None))
    for s, p, o in rights_spo:
        if s in valid_contexts:
            raise ValueError("%s: non-content or multiple publication rights" % s)
        valid_contexts[s] = CONTEXTS_FOR_RIGHTS[o]

    renditions_to_add = []

    for content_id in content:
        if not isinstance(content_id, rdflib.BNode) and not content_id.startswith(cfg.id_base):
            # external content is noted, but we will not render it, see above
            content[content_id] = CONTENT_EXTERNAL
            continue

        g.add((content_id, F.published, rdflib.Literal(datetime.datetime.now().isoformat(), datatype=XSD.dateTime)))

        # find rights info
        if content_id not in valid_contexts:
            logging.debug("%s: no publication rights specified, defaulting to public" % content_id)
            valid_contexts[content_id] = CONTEXTS_FOR_RIGHTS[F.public]
            g.add((content_id, F.hasPublicationRights, F.public))

        # look for markdown
        md = g.triples((content_id, F.markdown, None))
        for spo in md:
            o = spo[2]

            if F.page not in valid_contexts[content_id]:
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

                content[s] = CONTENT_READY

                html = mdproc.convert(o)
                for url in mdproc.images:
                    uriref = rdflib.URIRef(url)
                    if uriref in content:
                        logging.debug("%s: found embed of %s" % (s, url))
                        g.add((s, F.incorporates, uriref))
                    elif uriref in private_ids:
                        logging.debug("%s: found embed of private entity %s, doing nothing" % (s, url))
                    elif uriref.startswith(cfg.id_base):
                        raise ValidationError("%s: tried to embed non-document %s" % (s, url))
                    else:
                        logging.debug("%s: found embed of Web document %s" % (s, url))

                for url in mdproc.links:
                    uriref = rdflib.URIRef(url)
                    if uriref in content:
                        logging.debug("%s: found link to %s" % (s, url))
                        g.add((s, F.links, uriref))
                    elif uriref in private_ids:
                        logging.info("%s: found mention of private entity %s, doing nothing" % (s, url))
                    elif uriref.startswith(cfg.id_base):
                        logging.info("%s: found mention of %s" % (s, url))
                        g.add((s, F.mentions, uriref))
                    else:
                        logging.debug("%s: found link to Web document %s" % (s, url))



    for ctx, id_to_file in files.items():
        for content_id, fn in id_to_file.items():
            if content_id not in valid_contexts or ctx not in valid_contexts[content_id]:
                continue
            for mime, ext in FILE_TYPES.items():
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

                if content_id in content:
                    # we've got something, even if it may be intended for a different context
                    # TODO: figure out if this is a bad idea
                    content[content_id] = CONTENT_READY

                if mime == 'text/markdown':
                    if not blob:
                        blob = open(fn,'rb').read()
                    html = mdproc.convert(blob.decode('utf-8'))
                    for url in mdproc.images:
                        uriref = rdflib.URIRef(url)
                        if uriref in content:
                            logging.debug("%s: found link to %s" % (content_id, url))
                            g.add((content_id, F.incorporates, uriref))
                        else:
                            logging.warning("%s: found link to non-document %s %s" % (content_id, url, content))

    # markdown properties have all been converted or discarded
    g.remove((None, F.markdown, None))

    # graph is now ready
    for r in renditions_to_add:
        blob_id = add_rendition(g, cfg.ipfs_client, cfg.ipfs_namespace, **r)

    return g
