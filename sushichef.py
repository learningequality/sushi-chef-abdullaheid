#!/usr/bin/env python

from bs4 import BeautifulSoup
import codecs
from collections import defaultdict, OrderedDict
import copy
from git import Repo
import glob
from le_utils.constants import licenses, content_kinds, file_formats
import hashlib
import json
import logging
import markdown2
import ntpath
import os
from pathlib import Path
import re
import requests
from ricecooker.classes.licenses import get_license
from ricecooker.chefs import JsonTreeChef
from ricecooker.utils import downloader, html_writer
from ricecooker.utils.caching import CacheForeverHeuristic, FileCache, CacheControlAdapter
from ricecooker.utils.jsontrees import write_tree_to_json_tree, SUBTITLES_FILE
import time
from urllib.error import URLError
from urllib.parse import urljoin
from utils import if_dir_exists, get_name_from_url, clone_repo, build_path
from utils import if_file_exists, get_video_resolution_format, remove_links
from utils import get_name_from_url_no_ext, get_node_from_channel, get_level_map
from utils import remove_iframes, get_confirm_token, save_response_content
import youtube_dl


BASE_URL = "http://www.abdullaheid.net/"

DATA_DIR = "chefdata"
COPYRIGHT_HOLDER = "Abdullaheid"
LICENSE = get_license(licenses.SPECIAL_PERMISSIONS, 
        copyright_holder=COPYRIGHT_HOLDER,
        description="الحقوق متاحة لجميع الناس لغير الأغراض التجارية").as_dict()
#COUNTER_TITLE_KEYS = defaultdict(int)

LOGGER = logging.getLogger()
__logging_handler = logging.StreamHandler()
LOGGER.addHandler(__logging_handler)
LOGGER.setLevel(logging.INFO)

DOWNLOAD_VIDEOS = True

sess = requests.Session()
cache = FileCache('.webcache')
basic_adapter = CacheControlAdapter(cache=cache)
forever_adapter = CacheControlAdapter(heuristic=CacheForeverHeuristic(), cache=cache)
sess.mount('http://', basic_adapter)
sess.mount(BASE_URL, forever_adapter)

# Run constants
################################################################################
CHANNEL_NAME = "Abdulla Eid Network (العربيّة)"              # Name of channel
CHANNEL_SOURCE_ID = "sushi-chef-abdulla-eid-network-ar"    # Channel's unique id
CHANNEL_DOMAIN = "abdullaheid.net"          # Who is providing the content
CHANNEL_LANGUAGE = "ar"      # Language of channel
CHANNEL_DESCRIPTION = None                                  # Description of the channel (optional)
CHANNEL_THUMBNAIL = None                                    # Local path or url to image file (optional)

# Additional constants
################################################################################

class PageParser:
    def __init__(self, page_url):
        self.page_url = page_url
        self.page = self.to_soup()

    def to_soup(self):
        document = download(self.page_url)
        if document is not None:
            return BeautifulSoup(document, 'html.parser') #html5lib

    def get_sections(self):
        section_nodes = self.page.findAll(lambda tag: tag.name == "div" and tag.findChildren("h2", class_="color-blue"))
        for section_node in section_nodes:
            section = Section(section_node)
            #print(section.title)
            #print(section.description)
            #for link in section.links():
            #    print(link)
            yield section

    def write_videos(self):
        path = [DATA_DIR] + ["abdullah_videos"]
        path = build_path(path)
        for section in self.get_sections():
            LOGGER.info(section.title)
            section.download(download=DOWNLOAD_VIDEOS, base_path=path)
            yield section.to_node()
            break


class Section:
    def __init__(self, section_node, lang="ar"):
        self.html_node = section_node
        self.tree_nodes = OrderedDict()
        self.lang = lang

    @property
    def title(self):
        return self.html_node.find("h2").text

    @property
    def description(self):
        return self.html_node.find("p").text

    def links(self):
        ol = self.html_node.find(lambda tag: tag.name == "ol" and\
        tag.findParent("div", class_="list-wrapper clearfix"))
        for li in ol.findAll("li"):
            a = li.find("a")
            yield a.text, a.attrs.get("href", "")
            break

    def download(self, download=True, base_path=None):
        for name, link in self.links():
            youtube = YouTubeResource(link, name=name, lang=self.lang)
            youtube.download(download, base_path)
            node = youtube.to_node()
            if node["source_id"] not in self.tree_nodes:
                self.tree_nodes["source_id"] = node

    def to_node(self):
        topic_node = dict(
            kind=content_kinds.TOPIC,
            source_id=self.title,
            title=self.title,
            description=self.description,
            language=self.lang,
            license=LICENSE,
            children=self.tree_nodes.values()
        )
        return topic_node


class YouTubeResource(object):
    def __init__(self, source_id, name=None, type_name="Youtube", lang="en", embeded=False):
        LOGGER.info("Resource Type: "+type_name)
        self.filename = None
        self.type_name = type_name
        self.filepath = None
        self.name = name
        if embeded is True:
            self.source_id = YouTubeResource.transform_embed(source_id)
        else:
            self.source_id = self.clean_url(source_id)
        self.file_format = file_formats.MP4
        self.lang = lang
        self.is_valid = False

    def clean_url(self, url):
        if url[-1] == "/":
            url = url[:-1]
        return url.strip()

    @classmethod
    def is_youtube(self, url, get_channel=False):
        youtube = url.find("youtube") != -1 or url.find("youtu.be") != -1
        if get_channel is False:
            youtube = youtube and url.find("user") == -1 and url.find("/c/") == -1
        return youtube

    @classmethod
    def transform_embed(self, url):
        url = "".join(url.split("?")[:1])
        return url.replace("embed/", "watch?v=").strip()

    def get_video_info(self, download_to=None, subtitles=True):
        ydl_options = {
                'writesubtitles': subtitles,
                'allsubtitles': subtitles,
                'no_warnings': True,
                'restrictfilenames':True,
                'continuedl': True,
                'quiet': False,
                'format': "bestvideo[height<={maxheight}][ext=mp4]+bestaudio[ext=m4a]/best[height<={maxheight}][ext=mp4]".format(maxheight='480'),
                'outtmpl': '{}/%(id)s'.format(download_to),
                'noplaylist': False
            }

        with youtube_dl.YoutubeDL(ydl_options) as ydl:
            try:
                ydl.add_default_info_extractors()
                info = ydl.extract_info(self.source_id, download=(download_to is not None))
                return info
            except(youtube_dl.utils.DownloadError, youtube_dl.utils.ContentTooShortError,
                    youtube_dl.utils.ExtractorError) as e:
                LOGGER.info('An error occured ' + str(e))
                LOGGER.info(self.source_id)
            except KeyError as e:
                LOGGER.info(str(e))

    def subtitles_dict(self):
        subs = []
        video_info = self.get_video_info()
        if video_info is not None:
            video_id = video_info["id"]
            if 'subtitles' in video_info:
                subtitles_info = video_info["subtitles"]
                for language in subtitles_info.keys():
                    subs.append(dict(file_type=SUBTITLES_FILE, youtube_id=video_id, language=language))
        return subs

    #youtubedl has some troubles downloading videos in youtube,
    #sometimes raises connection error
    #for that I choose pafy for downloading
    def download(self, download=True, base_path=None):
        if not "watch?" in self.source_id or "/user/" in self.source_id or\
            download is False:
            return

        download_to = build_path([base_path, 'videos'])
        for i in range(4):
            try:
                info = self.get_video_info(download_to=download_to, subtitles=False)
                if info is not None:
                    LOGGER.info("Video resolution: {}x{}".format(info.get("width", ""), info.get("height", "")))
                    self.filepath = os.path.join(download_to, "{}.mp4".format(info["id"]))
                    self.filename = info["title"]
                    if self.filepath is not None and os.stat(self.filepath).st_size == 0:
                        LOGGER.info("Empty file")
                        self.filepath = None
            except (ValueError, IOError, OSError, URLError, ConnectionResetError) as e:
                LOGGER.info(e)
                LOGGER.info("Download retry")
                time.sleep(.8)
            except (youtube_dl.utils.DownloadError, youtube_dl.utils.ContentTooShortError,
                    youtube_dl.utils.ExtractorError, OSError) as e:
                LOGGER.info("An error ocurred, may be the video is not available.")
                return
            except OSError:
                return
            else:
                return

    def to_node(self):
        if self.filepath is not None:
            files = [dict(file_type=content_kinds.VIDEO, path=self.filepath)]
            files += self.subtitles_dict()
            node = dict(
                kind=content_kinds.VIDEO,
                source_id=self.source_id,
                title=self.name if self.name is not None else self.filename,
                description='',
                files=files,
                language=self.lang,
                license=LICENSE
            )
            return node


def download(source_id):
    tries = 0
    while tries < 4:
        try:
            document = downloader.read(source_id, loadjs=False, session=sess)
        except requests.exceptions.HTTPError as e:
            LOGGER.info("Error: {}".format(e))
        except requests.exceptions.ConnectionError:
            ### this is a weird error, may be it's raised when the webpage
            ### is slow to respond requested resources
            LOGGER.info("Connection error, the resource will be scraped in 5s...")
            time.sleep(3)
        except requests.exceptions.TooManyRedirects as e:
            LOGGER.info("Error: {}".format(e))
        else:
            return document
        tries += 1
    return False


# The chef subclass
################################################################################
class AbdullaheidChef(JsonTreeChef):
    HOSTNAME = BASE_URL
    TREES_DATA_DIR = os.path.join(DATA_DIR, 'trees')
    SCRAPING_STAGE_OUTPUT_TPL = 'ricecooker_json_tree.json'
    THUMBNAIL = ""

    def __init__(self):
        build_path([AbdullaheidChef.TREES_DATA_DIR])
        self.scrape_stage = os.path.join(AbdullaheidChef.TREES_DATA_DIR, 
                                AbdullaheidChef.SCRAPING_STAGE_OUTPUT_TPL)
        super(AbdullaheidChef, self).__init__()

    def download_css_js(self):
        r = requests.get("https://raw.githubusercontent.com/learningequality/html-app-starter/master/css/styles.css")
        with open("chefdata/styles.css", "wb") as f:
            f.write(r.content)

        r = requests.get("https://raw.githubusercontent.com/richleland/pygments-css/master/default.css")
        with open("chefdata/highlight_default.css", "w") as f:
            f.write(r.content.decode("utf-8").replace(".highlight", ".codehilite"))

        r = requests.get("https://raw.githubusercontent.com/learningequality/html-app-starter/master/js/scripts.js")
        with open("chefdata/scripts.js", "wb") as f:
            f.write(r.content)

    def pre_run(self, args, options):
        css = os.path.join(os.path.dirname(os.path.realpath(__file__)), "chefdata/styles.css")
        js = os.path.join(os.path.dirname(os.path.realpath(__file__)), "chefdata/scripts.js")
        if not if_file_exists(css) or not if_file_exists(js):
            LOGGER.info("Downloading styles")
            self.download_css_js()
        self.scrape(args, options)

    def scrape(self, args, options):
        LANG = 'ar'
        global channel_tree
        channel_tree = dict(
                source_domain=AbdullaheidChef.HOSTNAME,
                source_id=BASE_URL,
                title=CHANNEL_NAME,
                description="""لا يمكن لك أن تبني منزلاً بدون أساس, وكذلك هي بقية الأمور في الحياة, فكل شئ يبنى على أساس وأنت عزيزي المبرمج يجب عليك أن تبني علمك لأسس البرمجة على أساس قوي لكي يسهل عليك بعدها الخوض في معظم إن لم يكن كل المسائل البرمجية التي تمر عليك وتعمل عليها دون مشاكل تذكر, وللحصول على ذلك فلا تكن كالمستحي الذي ينتظر المعلومة لتأتي إليه ولا تكن كالمتكبر الذي يقف على جبل فيرى الناس صغاراً ويرونه صغيرا, اعد ترتيب أوراقك وابدأ بمنهجية ورتب وقتك وابدأ في بناء أساس قوي يجعلك بإذن الله قادر على أن تبني أقوى وأصعب البرامج دون توقف"""
[:400], #400 UPPER LIMIT characters allowed 
                thumbnail=None,
                language=LANG,
                children=[],
                license=LICENSE,
            )

        page_parser = PageParser(BASE_URL)
        print(list(page_parser.write_videos()))
        #page_parser.get_sections()


# CLI
################################################################################
if __name__ == '__main__':
    chef = AbdullaheidChef()
    chef.main()
