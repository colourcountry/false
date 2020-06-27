#!/usr/bin/python3

import rdflib
from rdflib.namespace import RDF, RDFS, DC, SKOS, OWL, XSD
import logging, os, re, io, datetime, markdown, urllib.parse, json, posixpath, time, subprocess
from zlib import adler32

F = rdflib.Namespace("http://id.colourcountry.net/false/")

# rdflib will happily save relative-looking URIs, but it puts "file:///" in front when loading them :(
# so we'll temporarily use a URI scheme, and make good on final publish
IPFS = rdflib.Namespace("ipfs:/")

log_handlers=[logging.StreamHandler()]
log_handlers[0].setLevel(logging.INFO)

try:
    log_handlers.append(logging.FileHandler(os.environ["FALSE_LOG_FILE"]))
except KeyError:
    pass
logging.basicConfig(level=logging.DEBUG,handlers=log_handlers)

# TODO make these configurable
EXTENSIONS = {
    "html": "text/html",
    "md": "text/markdown",
    "abc": "text/vnd.abc",

    "jpg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",

    "pdf": "application/pdf",
    "apk": "application/vnd.android.package-archive",
}

CONTEXTS = {
    "@teaser": F.teaser,
    "@embed": F.embed,
    "@page": F.page,
    "@download": F.download,
}

# A RENDITION KEY distinguishes different renditions of the same content, so that conversion processes
# can tell if they are stale. Rendition keys don't go into the graph or get exposed to IPFS or anything.
RENDITION_KEYS = {
     F.teaser: "@teaser",
     F.embed: "@embed",
     F.page: "@page",
     F.download: "@download"
}

teaser_convert_command = "convert -verbose -auto-orient -resize 400x225^ -gravity center -crop 400x225+0+0".split(" ")
embed_convert_command = "convert -verbose -auto-orient -resize 800x800>".split(" ")
page_convert_command = "convert -verbose -auto-orient -resize 1200x1200>".split(" ")

copy = lambda src,dest: ["cp",src,dest]

# binary files can't be presented on pages, embedded, or processed into teasers
convert_binary = { F.download: copy }

# document files have a way to embed them on pages in addition (managed by a template)
convert_document = { F.embed: copy, F.page: copy, F.download: copy }

# images can be converted into teasers by imagemagick as well as all the above.
convert_image = {
    F.teaser: lambda src,dest: teaser_convert_command+[src,dest],
    F.embed: lambda src,dest: embed_convert_command+[src,dest],
    F.page: lambda src,dest: page_convert_command+[src,dest],
    F.download: copy,
}

# embeddable files can't be processed into teasers and are not separately downloadable
convert_embeddable = { F.embed: copy, F.page: copy  }


CONVERSIONS = {
    "jpg": convert_image,
    "png": convert_image,
    "gif": convert_image,
    "md": convert_embeddable,
    "html": convert_embeddable,
    "abc": convert_document,
    "apk": convert_binary,
    "pdf": convert_document,
}

def ipfs_add_dir(dirpath):
    r = subprocess.run(["ipfs","add","-Qnr",dirpath], stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()
    # Remove the "n" to actually add to IPFS. With "n", hashes and copies only.
    # FIXME: make accessible to user
    logging.info(f"{r} added from {dirpath}")
    return r

def get_existing_hash(blob, entity_dir, blob_path):
    # FIXME: this will only catch changes to the blob, not to the info (is this bad?)

    id_cache_file = entity_dir+".ipfs-hash"
    try:
        existing_hash = open(id_cache_file,"rb").read()
    except FileNotFoundError:
        logging.info(f"{entity_dir}: no existing hash available")
        return

    try:
        existing_size = os.path.getsize(blob_path)
    except OSError:
        existing_size = 0

    if len(blob) != existing_size:
        logging.info(f"{entity_dir}: blob has changed size")
        return

    old_blob = open(blob_path,"rb").read()

    if old_blob != blob:
        logging.info(f"{entity_dir}: blob has changed")
        return

    return existing_hash


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
    def __init__(self, work_dir, id_base):
        self.g = rdflib.Graph()
        self.work_dir = work_dir
        os.makedirs(work_dir, exist_ok=True)
        self.id_base = id_base

        # Content can appear in these contexts.
        self.contexts_for_ava = {
                    F.public: {F.link, F.teaser, F.embed, F.page, F.download},
                    F.embeddable: {F.link, F.teaser, F.embed},
                    F.restricted: {F.link, F.teaser}
        }

        self.files = {ctx: {} for ctx in set(CONTEXTS.values())}

    def add_ttl(self, filename):
        self.g.load(filename, format='ttl', publicID=self.id_base)
        logging.info(f"loading rdf from {filename}")
        return self

    def add_dir(self,src_root):
        def path_to_id(path, url=''):
            if not path:
                return url
            p, s = os.path.split(path)
            if not s:
                return url
            if url:
                return path_to_id(p, os.path.join(s, url))
            return path_to_id(p, s)

        logging.info("** Looking for media in graph **")


        for path, dirs, files in os.walk(src_root, followlinks=True):
            logging.debug(f"Looking in {path}")

            # this is evil: https://cyluun.github.io/blog/manipulating-python-oswalk
            # the [:] makes sure dirs is altered in place so that os.walk uses the new one :cackle:
            dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('_')]

            for f in files:
                fullf = os.path.join(path,f)
                if f.endswith('.ttl'):
                    self.add_ttl(fullf)
                else:
                    # look for renditions of the entities that might be referenced

                    for pfx, ctx in CONTEXTS.items():
                        m = re.match("(.*)[.]([^.]*)$",f)
                        if not m or m.group(2) not in EXTENSIONS:
                            logging.debug(f"ignoring file without extension: {path}/{f}")
                            continue
                        ext = m.group(2)


                        pm = re.match("(.*)"+pfx+"[.]([^.]*)$",f)
                        if pm:
                            entity_id = rdflib.URIRef(urllib.parse.urljoin(self.id_base, path_to_id(path[len(src_root):], pm.group(1))))

                            if not path.startswith(src_root):
                                raise ValueError(f"expected path under {src_root}, got {path}")

                            logging.info(f"{entity_id}@@{ctx}: adding {fullf}")
                            self.files[ctx][entity_id] = (fullf, ext, False)
                            continue
                        else:
                            entity_id = rdflib.URIRef(urllib.parse.urljoin(self.id_base, path_to_id(path[len(src_root):], m.group(1))))

                            if entity_id in self.files[ctx]:
                                logging.info(f"{entity_id}@@{ctx}: found a context-specific file")
                                continue

                            logging.debug(f"{entity_id}@@{ctx}: will convert {fullf}")
                            self.files[ctx][entity_id] = (fullf, ext, True)

        return self


    def _add_markdown_refs(self, content_id, blob):
        mdproc = markdown.Markdown(extensions=[ImgExtExtension(base=self.id_base), 'tables'])
        html = mdproc.convert(blob)

        try:
          imgs = mdproc.images
        except AttributeError:
          imgs = []

        try:
          links = mdproc.links
        except AttributeError:
          links = []

        for url in imgs:
            uriref = rdflib.URIRef(url)
            if uriref in self.private_ids:
                logging.debug(f"{content_id}: found embed of private entity {url}, doing nothing")
            elif uriref in self.content:
                logging.debug(f"{content_id}: found embed of known content {url}")
                self.g.add((content_id, F.incorporates, uriref))
            elif uriref in self.entities:
                # an "embed" of a non-content is just a long-winded mention
                logging.debug(f"{content_id}: found embed of known entity {url}")
                self.g.add((content_id, F.mentions, uriref))
            else:
                logging.debug(f"{content_id}: found embed of unknown entity {url}, junking it")
                pass # legacy image

        for url in links:
            uriref = rdflib.URIRef(url)
            if uriref in self.private_ids:
                logging.debug(f"{content_id}: found link to private entity {url}, doing nothing")
            elif uriref in self.content:
                logging.debug(f"{content_id}: found mention of known content {url}")
                self.g.add((content_id, F.mentions, uriref))
            elif uriref in self.entities:
                logging.debug(f"{content_id}: found mention of known entity {url}")
                self.g.add((content_id, F.mentions, uriref))
            else:
                self.g.add((content_id, F.links, uriref))
                if uriref.startswith(self.id_base):
                    raise ValueError(f"{content_id}: link to undefined entity within ID scheme: {url}\nContent was: {blob}")
                logging.warning(f"{content_id}: found link to undefined entity {url}, assuming web page")
                self.g.add((uriref, RDF.type, F.WebPage))


    def _directorize_entity_id(self, entity_id):
        if isinstance(entity_id, rdflib.BNode):
            return ["blank",self.entities[entity_id]]

        if not isinstance(entity_id, rdflib.URIRef):
            return ["special",os.path.join(*entity_id.split("/"))]

        if entity_id.startswith("/ipfs/"):
            raise ValueError(entity_id)

        entity_id = urllib.parse.urljoin(self.id_base, entity_id)

        if entity_id.startswith(self.id_base):
            return ["local"]+entity_id[len(self.id_base):].split("/")

        p = urllib.parse.urlparse(entity_id)

        return [p.netloc]+p.path.split("/")+p.fragment.split("/")

    def _make_entity_dir(self, entity_id, rendition_key=""):
        # if there's an id hint already, use that (mostly for blank nodes)
        entity_dir = os.path.join(*self._directorize_entity_id(entity_id))

        entity_dir = os.path.join(self.work_dir, entity_dir, rendition_key) # URIRef or BNode, normally

        os.makedirs(entity_dir, exist_ok=True)

        return entity_dir

    def _get_converted_file(self, entity_id, entity_dir, fn, ext, ctx, rendition_key, blob_filename):
        # If there's not an existing file then we have to convert
        existing_converted_path = os.path.join(entity_dir,rendition_key,blob_filename) # FIXME this is a bit spaghetti
        if not os.path.exists(existing_converted_path):
            # FIXME: this process doesn't return the converted path above, but just __last_conversion,
            # and we rely on the blob reading to pick it up and add it, which feels odd
            return self._convert_file(entity_id, entity_dir, fn, ext, ctx)

        # Assume that if the original file hasn't changed then its conversions are also ok
        existing_path = os.path.join(entity_dir,"original."+ext)

        r = subprocess.run(["diff", existing_path, fn])

        if r.returncode!=0:
            logging.info(f"{entity_id}@@{ctx}: file {fn} has changed")
            return self._convert_file(entity_id, entity_dir, fn, ext, ctx)

        logging.debug(f"{entity_id}@@{ctx}: file has not changed")

        return existing_converted_path

    def _convert_file(self, entity_id, entity_dir, fn, ext, ctx):
        converted_file = os.path.join(self.work_dir,"__last_conversion."+ext)
        logging.info(f"{entity_id}@@{ctx}: converting {fn}")
        r = subprocess.run(CONVERSIONS[ext][ctx](fn,converted_file))
        return converted_file


    def _add_rendition(self, mediaType, entity_id, rendition_key, blob=None, blob_filename=None, **properties):
        info_blob = None
        info = rdflib.Graph()
        info.bind('', F)

        # add everything we know about this entity
        for p, o in self.g[entity_id]:
            if p != F.rendition: # we might know about other renditions already, but it's pot luck, so best to keep just this one
                info.add((entity_id, p, o))

        blob_uri = rdflib.URIRef(blob_filename)
        info.add((entity_id, F.rendition, blob_uri)) # relative path to the file, as we don't know the hash
        info.add((blob_uri, F.mediaType, mediaType))

        info_blob = info.serialize(format='ttl')

        entity_dir = self._make_entity_dir(entity_id, rendition_key)

        if info_blob:
          open(os.path.join(entity_dir,"info.ttl"),"wb").write(info_blob)

        blob_path = os.path.join(entity_dir,blob_filename)
        ipfs_hash = get_existing_hash(blob, entity_dir, blob_path)

        if not ipfs_hash:
            open(blob_path,"wb").write(blob)
            ipfs_hash = ipfs_add_dir(entity_dir)
            open(entity_dir+".ipfs-hash","wb").write(ipfs_hash)

        ipfs_id = IPFS[ipfs_hash.decode("us-ascii")+"/"+blob_filename]

        self.g.add((ipfs_id, RDF.type, F.Media))
        self.g.add((ipfs_id, F.mediaType, mediaType))
        self.g.add((ipfs_id, F.blobURL, ipfs_id)) # in IPFS, IDs and URLs are the same thing
        self.g.add((ipfs_id, F.localPath, rdflib.Literal(entity_dir))) # used (and removed) by the publisher to avoid IPFS round trips

        for k, v in properties.items():
            logging.debug(f"{entity_id}: adding property {k}={v}")
            self.g.add((ipfs_id, F[k], v))
        self.g.add((entity_id, F.rendition, ipfs_id))
        logging.debug(f"{entity_id}: finished adding rendition {ipfs_id}")
        return ipfs_id

    def build(self):
        self.g.bind('ipfs', IPFS)

        # first remove all private stuff, we don't want to know about it, convert it, or add it to IPFS
        self.private_ids = set()

        logging.debug( list(self.g.triples((None, F.hasAvailability, F.private))) )
        for triple in self.g.triples((None, F.hasAvailability, F.private)):
            entity_id = triple[0]
            logging.info(f"{entity_id}: dropping private entity")
            self.private_ids.add(entity_id)
            self.g.remove((entity_id, None, None))
            self.g.remove((None, entity_id, None))
            self.g.remove((None, None, entity_id))

        self.entities = {} # id: savedir hint
        self.content = set()
        self.valid_contexts = {}

        doc_types = [x[0] for x in self.g.query("""select ?t where { ?t rdfs:subClassOf+ :Content }""")]


        type_spo = self.g.triples((None, RDF.type, None))
        for s, p, o in type_spo:
            self.entities[s] = s
            if o == F.Content or o in doc_types:
                logging.debug(f"{s}: is content ({s.__class__.__name__})")
                self.content.add(s)

        rights_spo = self.g.triples((None, F.hasAvailability, None))
        for s, p, o in rights_spo:
            if s in self.valid_contexts:
                raise ValueError(f"{s}: multiple availability values")
            self.valid_contexts[s] = self.contexts_for_ava[o]

        for entity_id in self.entities:
            if entity_id not in self.valid_contexts:
                logging.debug(f"{entity_id}: no availability specified, defaulting to public")
                self.valid_contexts[entity_id] = self.contexts_for_ava[F.public]
                self.g.add((entity_id, F.hasAvailability, F.public))

        renditions_to_add = []

        # add renditions for :markdown properties
        for content_id in self.content:
            md = self.g.triples((content_id, F.markdown, None))
            for spo in md:
                s, p, o = spo

                if isinstance(content_id,rdflib.term.BNode):
                    checksum = adler32(o.encode("utf-8"))
                    blob_filename = re.sub("[\W]","_",o.strip()[:20])+str(checksum)+".md"
                    self.entities[content_id] = rdflib.term.BNode(blob_filename)
                else:
                    blob_filename = posixpath.basename(content_id)+".md"

                logging.debug(f"{content_id}: adding rendition for markdown property: {o[:50].strip()}")
                renditions_to_add.append({
                    'entity_id': content_id,
                    'blob': o.encode('utf-8'),
                    'blob_filename': blob_filename,
                    'rendition_key': RENDITION_KEYS[F.page],
                    'mediaType': rdflib.Literal('text/markdown'),
                    'charset': rdflib.Literal('utf-8'),
                    'intendedUse': F.page # embeds will fall back to this, but it can be distinguished from stuff to offer as a download
                })

                self._add_markdown_refs(s, o)

        originals_to_copy = {}
       
        for ctx, id_to_file in self.files.items():
            for entity_id, (fn, ext, needs_conversion) in id_to_file.items():
                if entity_id not in self.valid_contexts:
                    logging.debug(f"{entity_id} is not defined or not valid for {ctx}, dropping")
                    continue

                if ctx not in self.valid_contexts[entity_id]:
                    continue

                rendition_key = RENDITION_KEYS[ctx]

                blob_filename = posixpath.basename(entity_id)+"."+ext

                if needs_conversion:
                    if ext not in CONVERSIONS:
                        logging.warning(f"{entity_id}: no conversions available for {EXTENSIONS[ext]}")
                        continue
                    if ctx not in CONVERSIONS[ext]:
                        logging.warning(f"{entity_id}@@{ctx}: no conversion available for {EXTENSIONS[ext]}")
                        continue

                    entity_dir = self._make_entity_dir(entity_id)
                    if fn not in originals_to_copy:
                        originals_to_copy[fn] = os.path.join(entity_dir,"original."+ext)

                    fn = self._get_converted_file(entity_id, entity_dir, fn, ext, ctx, rendition_key, blob_filename)

                blob = open(fn,'rb').read()
                logging.debug(f"{entity_id}: found rendition at {fn}")
                renditions_to_add.append({
                    'entity_id': entity_id,
                    'blob': blob,
                    'blob_filename': blob_filename,
                    'rendition_key': rendition_key,
                    'mediaType': rdflib.Literal(EXTENSIONS[ext]),
                    'charset': rdflib.Literal("utf-8"),
                    'intendedUse': ctx
                })

                if EXTENSIONS[ext] == 'text/markdown':
                    if not blob:
                        blob = open(fn,'rb').read()
                    self._add_markdown_refs(content_id, blob.decode('utf-8'))

        # now all contexts are converted, we can copy the new originals
        for src, dest in originals_to_copy.items():
            subprocess.run(["cp",src,dest])

        # markdown properties have all been converted or discarded
        self.g.remove((None, F.markdown, None))

        # graph is now ready
        for r in renditions_to_add:
            blob_id = self._add_rendition(**r)

        return self.g
