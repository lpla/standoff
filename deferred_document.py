#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
import base64
import html5lib
from lxml import etree
import argparse

import re

sys.setrecursionlimit(10000)

def remove_control_characters(html):
    # type: (t.Text) -> t.Text
    """
    Strip invalid XML characters that `lxml` cannot parse.
    """
    # See: https://github.com/html5lib/html5lib-python/issues/96
    #
    # The XML 1.0 spec defines the valid character range as:
    # Char ::= #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]
    #
    # We can instead match the invalid characters by inverting that range into:
    # InvalidChar ::= #xb | #xc | #xFFFE | #xFFFF | [#x0-#x8] | [#xe-#x1F] | [#xD800-#xDFFF]
    #
    # Sources:
    # https://www.w3.org/TR/REC-xml/#charsets,
    # https://lsimons.wordpress.com/2011/03/17/stripping-illegal-characters-out-of-xml-in-python/
    def strip_illegal_xml_characters(s, default, base=10):
        # Compare the "invalid XML character range" numerically
        n = int(s, base)
        if n in (0xb, 0xc, 0xFFFE, 0xFFFF) or 0x0 <= n <= 0x8 or 0xe <= n <= 0x1F or 0xD800 <= n <= 0xDFFF:
            return ""
        return default

    # We encode all non-ascii characters to XML char-refs, so for example "💖" becomes: "&#x1F496;"
    # Otherwise we'd remove emojis by mistake on narrow-unicode builds of Python
    html = html.decode('utf8').encode("ascii", "xmlcharrefreplace").decode("utf-8")
    html = re.sub(r"&#(\d+);?", lambda c: strip_illegal_xml_characters(c.group(1), c.group(0)), html)
    html = re.sub(r"&#[xX]([0-9a-fA-F]+);?", lambda c: strip_illegal_xml_characters(c.group(1), c.group(0), base=16), html)
    html = ILLEGAL_XML_CHARS_RE.sub("", html)
    return html


# A regex matching the "invalid XML character range"
ILLEGAL_XML_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1F\uD800-\uDFFF\uFFFE\uFFFF]")

#Inline tags that don't start on a new line and only take up as much width as necessary. From https://www.w3schools.com/html/html_blocks.asp
inline_tags={"a","abbr","acronym","b","bdo","big","br","button","cite","code","dfn","em","i","img","input","kbd","label","map","object","q","samp","script","select","small","span","strong","sub","sup","textarea","time","tt","var"}

def getWordStandoff(element,document,positionsdict):
    """Returns a list with word standoff annotations of a tree element of lxml and the corresponding text
    
    Returns a semicolon separated list of word standoffs from a XML/XHTML element and a string of plain text from HTML following browser parser separation of words. Updates the consumed positions of his parent if tail is found
    """
    #Variable for marking ending space in element text or implicit space of not-inline (block) tags
    global spaceEndPreviousTag
    #Return variables for standoff and plain text
    standoff=[]
    text=""
    wordposition=0      #Pointer of actual start word character inside the element tag
    wordpositionparent=positionsdict[element.getparent()]       #Pointer of actual start word character outside the element tag (tail), which belongs to the parent tag
    firstword = True    #Trigger for first word
    if element.text != None:        #If we have text in tag
        #Add interpreted space for non-inline tags
        if element.tag not in inline_tags:
            text = " "  #Add artificial separation as browser parser does if tag is not inline
            spaceEndPreviousTag = True  #Activate space flag to avoid glue standoff with '+' with previous tag

        text = text + element.text
        for word in re.split(r'(\s+)', element.text): #Split using spaces, but keeping those spaces as words (to count double spaces positions)
            if word.isspace():
                wordposition=wordposition+len(word)
                firstword=False
                spaceEndPreviousTag = True
            elif word == "":
                continue
            else:
                plusprevious = ""
                if firstword and not spaceEndPreviousTag: #If there is no space at the end of previous tag and we are processing the first word of this tag, concat the standoff with '+'
                    plusprevious = "+"
                #For every space separated word inside the tag, create a standoff annotation with the path from root, remove the w3 note from tag and the initial and final character positions. Then, update the position pointer
                standoff.append(plusprevious + document.getelementpath(element)+':'+str(wordposition)+"-"+str(wordposition+len(word)-1))
                wordposition=wordposition+len(word)
                spaceEndPreviousTag = False
                firstword=False
    positionsdict[element]=wordposition     #Insert the element pointer in the pointer dictionary (for future children with tail text)
    
    #Now we recursively iterate through the childs
    for child in element:
        if type(child) is etree._Element and child.tag != "script" and child.tag != "style":
            childstandoff, childtext = getWordStandoff(child,document,positionsdict)
            standoff = standoff + childstandoff
            text = text + childtext

    #Add interpreted space for non-n tags after all processing of the actual tag content
    if element.tag not in inline_tags:
        text = text + " "
        spaceEndPreviousTag = True
    elif element.tag == "br":
        text = text + " "
        spaceEndPreviousTag = True


    #Processing parent text (A.K.A. element.tail) similarly as the actual tag
    firstword = True
    if element.tail != None:        #If we have tail parent text (text after the closing tag until the next open/close tag)
        text = text + element.tail
        for word in re.split(r'(\s+)', element.tail):
            if word.isspace():
                wordpositionparent=wordpositionparent+len(word)
                firstword=False
                spaceEndPreviousTag = True
            elif word == "":
                continue
            else:
                plusprevious = ""
                if firstword and not spaceEndPreviousTag:
                    plusprevious = "+"
                #Generate word standoff annotation in the same way as the text inside the tag
                standoff.append(plusprevious + document.getelementpath(element.getparent())+':'+str(wordpositionparent)+"-"+str(wordpositionparent+len(word)-1))
                wordpositionparent=wordpositionparent+len(word)
                spaceEndPreviousTag = False
                firstword=False
        positionsdict[element.getparent()]=wordpositionparent       #Update the pointer dictionary with the parent pointer

    return standoff,text

def getDocumentStandoff(document):
    """Returns a list with word standoff annotations of a document tree in lxml format
    
    Generates Stand-off Annotation of a HTML document parsed to XHTML (see Forcada, Esplà-Gomis and Pérez-Ortiz, EAMT 2016) similar implementation as a set of word annotations
    """
    docstandoff=[]
    docplaintext=""
    positions={document.getroot():0}
    for element in document.getroot():
        if type(element) is etree._Element and element.tag != "script" and element.tag != "style": #Only elements, not scripts or other kind of tags without proper text
            wordstandoff,plaintext = getWordStandoff(element,document,positions)
            if wordstandoff:
                docstandoff=docstandoff+wordstandoff
                docplaintext=docplaintext+plaintext

    #Glue '+' marked annotations with previous standoff
    gluedStandoff = []
    buffered_standoff = None
    for annotation in docstandoff:
        if buffered_standoff == None:
            buffered_standoff = annotation
        elif annotation[0] == '+':
            buffered_standoff = buffered_standoff + annotation
        else:
            gluedStandoff.append(buffered_standoff)
            buffered_standoff = annotation
    if buffered_standoff is not None:
        gluedStandoff.append(buffered_standoff)
    
    return gluedStandoff,docplaintext


def main():
    #Global variable to mark if end of tag text has space of any kind
    spaceEndPreviousTag=True
    
    parser = argparse.ArgumentParser(description='Generates (stdout) Stand-off Annotation of HTML documents given in Bitextor crawl format (stdin)')
    
    args = parser.parse_args()
    
    #Input (stdin) in Bitextor crawl format:
    #html_content(base_64)      url
    
    #Output (stdout):
    #html_plain_text(base_64)   url document_standoff_annotation
    for line in sys.stdin:
        fields=line.split('\t')
        fields = list(map(str.strip, fields)) #Strip all elements
        document = html5lib.parse(remove_control_characters(base64.b64decode(fields[0])),treebuilder="lxml",namespaceHTMLElements=False) #We use lxml treebuilder because of getelementpath function and iteration through elements
        standoff,documenttext = getDocumentStandoff(document)
        fields.append(";".join(standoff))
        fields[0]=base64.b64encode(documenttext.encode('utf8')).decode('utf8')
        print('\t'.join(fields))

if __name__ == "__main__":
    main()
