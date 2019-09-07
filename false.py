#!/usr/bin/python3

import false.publish, false.build, false.config
import rdflib
from rdflib.namespace import RDF, DC, SKOS, OWL
import sys, logging, os, re, urllib.parse, datetime
import ipfshttpclient
import jinja2, markdown
import pprint

log_handlers=[logging.StreamHandler()]
log_handlers[0].setLevel(logging.INFO)
try:
    log_handlers.append(logging.FileHandler(os.environ["FALSE_LOG_FILE"]))
except KeyError:
    pass
logging.basicConfig(level=logging.DEBUG,handlers=log_handlers)

F = rdflib.Namespace("http://id.colourcountry.net/false/")


FILE_TYPES = { "text/html": ".html",
               "text/plain": ".txt",
               "text/markdown": ".md",
               "image/jpeg": ".jpg",
               "image/png": ".png",
               "application/pdf": ".pdf" }

# Local content can appear in these contexts
CONTEXTS = {
             # Auto-generated by convert-images
             ".teaser.": F.teaser,
             ".embed.": F.embed,
             ".page.": F.page,
             ".download.": F.download,
             # Manually specified
             "teaser.": F.teaser,
             "embed.": F.embed,
             "page.": F.page,
        }

def path_to_id(path, url=''):
    if not path:
        return url
    p, s = os.path.split(path)
    if not s:
        return url
    if url:
        return path_to_id(p, os.path.join(s, url))
    return path_to_id(p, s)

if __name__=="__main__":

    cfg = false.config.Config( src_dir=os.environ["FALSE_SRC"],
                          url_base=os.environ.get("FALSE_URL_BASE", None),
                          output_dir=os.environ.get("FALSE_OUT", None),
                          template_dir=os.environ.get("FALSE_TEMPLATES", None),
                          home_site=os.environ.get("FALSE_HOME_SITE", None),
                          id_base=os.environ.get("FALSE_ID_BASE", None))

    ipfs_namespace = rdflib.Namespace("/ipfs/")

    try:
        ipfs_client = ipfshttpclient.connect('/ip4/127.0.0.1/tcp/5001')
        cfg.setIPFS(ipfs_client, ipfs_namespace, "ipfs", os.environ.get("FALSE_IPFS_CACHE", None))
    except ipfshttpclient.exceptions.ConnectionError:
        logging.info("No IPFS daemon running. Trying to go ahead with mockipfs")

        import false.mockipfs
        ipfs_client = false.mockipfs.MockIPFS("ipfs")
        cfg.setIPFS(ipfs_client, ipfs_namespace, "ipfs", os.environ.get("FALSE_IPFS_CACHE", None))

    cfg.validate()

    g = rdflib.Graph()
    g.load(os.path.join(os.path.dirname(false.build.__file__),"false.ttl"), format='ttl')
    g.load(os.path.join(os.path.dirname(false.build.__file__),"false-xl.ttl"), format='ttl')

    files_by_ctx = {ctx: {} for ctx in set(CONTEXTS.values())}
    for path, dirs, files in os.walk(cfg.src_dir):
        for f in files:
            fullf = os.path.join(path,f)
            if f.endswith('.ttl'):
                logging.info("Loading %s from %s" % (f,path))
                g.load(fullf, format='ttl', publicID=cfg.id_base)
            else:
                for pfx, ctx in CONTEXTS.items():
                    if f.startswith(pfx):
                        basef = re.sub('^ipfs-[^.]+[.]', '', f[len(pfx):])
                        basef = re.sub('[.].*$', '', basef)
                        entity_id = rdflib.URIRef(urllib.parse.urljoin(cfg.id_base, path_to_id(path[len(cfg.src_dir):], basef)))
                        logging.info("%s@@%s: adding %s" % (entity_id, ctx, fullf))
                        files_by_ctx[ctx][entity_id] = fullf
                        break

    logging.info("** Building **")

    final_graph = false.build.Builder(g, cfg, files_by_ctx, FILE_TYPES).build()

    if not cfg.output_dir:
        raise SystemExit("No output dir specified, not publishing.")

    logging.info("** Publishing **")

    home_page = false.publish.publish_graph(final_graph, cfg)

    print(home_page)
