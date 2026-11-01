import math
import os
import re
import textwrap
from collections import Counter
 

try:
    import nltk
    from nltk.tokenize import sent_tokenize
except ImportError:  
    nltk = None
    sent_tokenize = None
 
_NLTK_READY = False
 
 
def _ensure_nltk_data():
    """Telecharge (une seule fois) les donnees NLTK necessaires."""
    global _NLTK_READY
    if _NLTK_READY or nltk is None:
        return
    _NLTK_READY = True
    needed = [
        ('tokenizers/punkt', 'punkt'),
        ('tokenizers/punkt_tab', 'punkt_tab'),
        ('corpora/stopwords', 'stopwords'),
    ]
    for path, name in needed:
        try:
            nltk.data.find(path)
        except LookupError:
            try:
                nltk.download(name, quiet=True)
            except Exception:
                pass
 
 

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None
 
try:
    from docx import Document
except ImportError:
    Document = None
 
 

BUILTIN_STOPWORDS = {
    'english': set("""
        a about above after again against all also am an and any are as at be because been before being
        below between both but by can could did do does doing down during each few for from further had
        has have having he her here hers herself him himself his how i if in into is it its itself just
        me more most my myself no nor not now of off on once only or other our ours ourselves out over
        own same she should so some such than that the their theirs them themselves then there these
        they this those through to too under until up very was we were what when where which while who
        whom why will with would you your yours yourself yourselves
    """.split()),
    'french': set("""
        a au aux avec ce ces cet cette dans de des du elle elles en es est et etaient etait ete etre
        eu il ils je la le les leur leurs lui ma mais me meme mes moi mon ne nos notre nous on ou par
        pas pour qu que qui sa se ses si son sur ta te tes toi ton tu un une vos votre vous y ont sont
        avoir fait faire plus tout tous toute toutes comme aussi donc car ni entre sans sous
        ainsi alors apres avant bien chaque cela celui celle ceux depuis encore etc lors dont ici
        peut peuvent doit doivent
    """.split()),
}
 
LANGUAGE_MARKERS = {
    'english': set("the and of to is are that with for this was were be have from by which".split()),
    'french': set("le la les des du et est une un que qui dans pour sur avec sont au aux ce par pas plus".split()),
}
 
LABELS = {
    'french': {
        'intro': "Ce document traite principalement de {topics}.",
        'and': "et",
        'empty': "Le document semble vide ou n'a pas pu etre lu.",
        'no_sentences': "Aucune phrase exploitable n'a ete trouvee dans le contenu.",
        'video': "Les fichiers video doivent d'abord etre transcrits en texte avant d'etre resumes.",
        'error': "Une erreur est survenue pendant le traitement : {err}",
        'try_again': "Essayez un autre fichier ou verifiez son format.",
        'title_overview': "RESUME",
        'title_points': "POINTS CLES",
    },
    'english': {
        'intro': "This document mainly deals with {topics}.",
        'and': "and",
        'empty': "The document appears to be empty or could not be read.",
        'no_sentences': "No usable sentences were found in the content.",
        'video': "Video files must first be transcribed to text before they can be summarized.",
        'error': "An error occurred while processing the content: {err}",
        'try_again': "Please try a different file or check the file format.",
        'title_overview': "SUMMARY",
        'title_points': "KEY POINTS",
    },
}
 
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
 
 
class UltimateSummarizer:
    """Resume extractif bilingue (francais / anglais)."""
 
    VIDEO_EXTENSIONS = ('.mp4', '.mov', '.avi')
 
    def __init__(self, min_sentence_words=7, max_sentence_words=60):
        self.min_sentence_words = min_sentence_words
        self.max_sentence_words = max_sentence_words
        self._stopword_cache = {}
        _ensure_nltk_data()
 
   
    def summarize_file(self, file_path):
        """Resume un fichier .pdf, .docx ou .txt."""
        try:
            ext = os.path.splitext(file_path.lower())[1]
            if ext in self.VIDEO_EXTENSIONS:
                return self._message_result('video', 'english', 'french')
 
            text = self._extract_text(file_path)
            return self._summarize(text, language=None, num_sentences=None,
                                   remove_headings=True)
        except Exception as e:
            return {
                'overview': LABELS['french']['error'].format(err=e) + " / " +
                            LABELS['english']['error'].format(err=e),
                'key_points': [LABELS['french']['try_again'],
                               LABELS['english']['try_again']],
            }
 
    def summarize_text(self, text, language=None, num_sentences=None):
        """Resume un texte brut (ex: transcription video).
 
        language : 'french', 'english' ou None (detection automatique)
        num_sentences : nombre de phrases du resume (None = automatique)
        """
        try:
            return self._summarize(text, language, num_sentences,
                                   remove_headings=False)
        except Exception as e:
            return {
                'overview': LABELS['french']['error'].format(err=e) + " / " +
                            LABELS['english']['error'].format(err=e),
                'key_points': [LABELS['french']['try_again'],
                               LABELS['english']['try_again']],
            }
 
    def format_summary(self, summary_result, language='french'):
        """Met en forme le resume pour l'affichage en console / fichier texte."""
        labels = LABELS.get(language, LABELS['english'])
        bar = '-' * 50
        points = '\n'.join('- ' + p for p in summary_result['key_points'])
        overview = '\n\n'.join(
            textwrap.fill(par, width=80)
            for par in summary_result['overview'].split('\n\n')
        )
        return (
            "\n" + labels['title_overview'] + "\n" + bar + "\n" + overview + "\n\n" +
            labels['title_points'] + "\n" + bar + "\n" + points + "\n"
        )
 
   
    def _summarize(self, text, language, num_sentences, remove_headings):
        text = self._clean_text(text or '', remove_headings)
        if not text.strip():
            return self._message_result('empty', 'french', 'english')
 
        if language not in LABELS:
            language = self._detect_language(text)
        labels = LABELS[language]
        stop = self._get_stopwords(language)
 
        sentences = [self._tidy_sentence(s) for s in self._split_sentences(text, language)]
        sentences = [s for s in sentences if s]
 
        
        if len(sentences) <= 2 and len(text.split()) > 80:
            sentences = self._chunk_words(text)
 
       
        candidates = [
            i for i, s in enumerate(sentences)
            if self.min_sentence_words <= len(s.split()) <= self.max_sentence_words
        ]
        if not candidates:
            candidates = [i for i, s in enumerate(sentences) if len(s.split()) >= 3]
        if not candidates:
            return self._message_result('no_sentences', language)
 
        
        if len(sentences) <= 3:
            return {
                'overview': ' '.join(sentences),
                'key_points': [self._truncate(s, 45) for s in sentences],
            }
 
       
        sentence_tokens = {}
        display_forms = {}   
        freq = Counter()
        for i, sent in enumerate(sentences):
            toks = []
            for raw in WORD_RE.findall(sent):
                low = raw.lower()
                if len(low) < 3 or low in stop:
                    continue
                norm = self._normalize(low)
                toks.append(norm)
                display_forms.setdefault(norm, Counter())[low] += 1
            sentence_tokens[i] = toks
            freq.update(toks)
 
        if not freq:
            return self._message_result('no_sentences', language)
 
        max_freq = max(freq.values())
        weight = {w: c / max_freq for w, c in freq.items()}
 
       
        total = len(sentences)
        scores = {}
        for i in candidates:
            toks = sentence_tokens[i]
            if not toks:
                scores[i] = 0.0
                continue
            base = sum(weight[t] for t in toks) / (len(toks) ** 0.6)
            position_bonus = 0.15 * (1 - i / total)
            scores[i] = base + position_bonus
 
       
        n_cand = len(candidates)
        if num_sentences is None:
            target = max(3, min(10, round(2 * math.sqrt(n_cand))))
        else:
            target = max(1, int(num_sentences))
        target = min(target, n_cand)
 
        ranked = sorted(candidates, key=lambda i: scores[i], reverse=True)
        selected = []
        for i in ranked:
            toks_i = set(sentence_tokens[i])
            if any(self._jaccard(toks_i, set(sentence_tokens[j])) > 0.5 for j in selected):
                continue
            selected.append(i)
            if len(selected) >= target:
                break
 
        
        top_keys = [w for w, _ in freq.most_common(5)]
        topics = [display_forms[w].most_common(1)[0][0] for w in top_keys]
        intro = labels['intro'].format(topics=self._join_list(topics, labels['and']))
 
        
        body_sentences = [sentences[i] for i in sorted(selected)]
        paragraphs = [
            ' '.join(body_sentences[k:k + 3])
            for k in range(0, len(body_sentences), 3)
        ]
        overview = '\n\n'.join([intro] + paragraphs)
 
       
        kp_count = min(7, max(3, target - 1))
        kp_idx = sorted(selected[:kp_count])
        key_points = [self._truncate(sentences[i], 45) for i in kp_idx]
 
        return {'overview': overview, 'key_points': key_points}
 
   
    def _extract_text(self, file_path):
        ext = os.path.splitext(file_path.lower())[1]
 
        if ext == '.pdf':
            if PyPDF2 is None:
                raise RuntimeError("PyPDF2 n'est pas installe / PyPDF2 is not installed")
            parts = []
            with open(file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        parts.append(page_text)
            return '\n'.join(parts)
 
        if ext == '.docx':
            if Document is None:
                raise RuntimeError("python-docx n'est pas installe / python-docx is not installed")
            doc = Document(file_path)
            return '\n'.join(p.text for p in doc.paragraphs if p.text.strip())
 
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
 
   
    def _clean_text(self, text, remove_headings):
        text = text.replace('\r', '\n')
       
        text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)
       
        text = re.sub(r'https?://\S+', ' ', text)
       
        text = re.sub(r'\[\s*\d{1,3}(?:\s*[,\u2013-]\s*\d{1,3})*\s*\]', '', text)
        
        text = re.sub(r'(?m)^\s*\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s+', '', text)
 
        lines = []
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue
            line = re.sub(r'^[\s\u2022\u25AA\u27A2\u2192\u00A7\u00B7*\-\u2013]+', '', line)
            if not line:
                continue
            if remove_headings and len(line.split()) < 6 and not re.search(r'[.!?\u2026:;\u00BB"\u201D)]$', line):
                continue  
            lines.append(line)
 
        text = ' '.join(lines)
        text = re.sub(r'[\u2022\u25AA\u27A2\u2192\u00A7]', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text
 
    def _split_sentences(self, text, language):
        if sent_tokenize is not None:
            try:
                return sent_tokenize(text, language=language)
            except Exception:
                pass
        return re.split(
            r'(?<=[.!?\u2026])\s+(?=[A-Z\u00C0-\u00D6\u00D8-\u00DE0-9\u00AB"\u201C(])', text
        )
 
    def _chunk_words(self, text, size=25):
        words = text.split()
        chunks = []
        for k in range(0, len(words), size):
            chunk = ' '.join(words[k:k + size])
            chunks.append(self._tidy_sentence(chunk))
        return [c for c in chunks if c]
 
    def _tidy_sentence(self, sentence):
        sentence = re.sub(r'\s+', ' ', sentence).strip()
        if not sentence:
            return ''
        sentence = sentence[0].upper() + sentence[1:]
        if sentence[-1] not in '.!?\u2026"\u201D\u00BB)':
            sentence += '.'
        return sentence
 
    
    def _detect_language(self, text):
        sample = text[:4000].lower()
        words = WORD_RE.findall(sample)
        en = sum(1 for w in words if w in LANGUAGE_MARKERS['english'])
        fr = sum(1 for w in words if w in LANGUAGE_MARKERS['french'])
        accents = sum(1 for ch in sample if ch in '\u00E9\u00E8\u00EA\u00E0\u00E7\u00F9\u00EE\u00F4\u00EB\u00EF\u00E2')
        if sample:
            fr += accents / max(1, len(sample)) * 100  
        return 'french' if fr > en else 'english'
 
    def _get_stopwords(self, language):
        if language in self._stopword_cache:
            return self._stopword_cache[language]
        words = set(BUILTIN_STOPWORDS[language])
        if nltk is not None:
            try:
                from nltk.corpus import stopwords
                words |= {w.lower() for w in stopwords.words(language)}
            except Exception:
                pass
        self._stopword_cache[language] = words
        return words
 
    @staticmethod
    def _normalize(word):
        """Reduit les pluriels simples (chats -> chat) pour regrouper les mots."""
        if len(word) > 4 and word.endswith('s') and not word.endswith('ss'):
            return word[:-1]
        return word
 
    @staticmethod
    def _jaccard(a, b):
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)
 
    @staticmethod
    def _truncate(sentence, max_words):
        words = sentence.split()
        if len(words) <= max_words:
            return sentence
        return ' '.join(words[:max_words]).rstrip(',;:') + '\u2026'
 
    @staticmethod
    def _join_list(items, and_word):
        if not items:
            return ''
        if len(items) == 1:
            return items[0]
        return ', '.join(items[:-1]) + ' ' + and_word + ' ' + items[-1]
 
    @staticmethod
    def _message_result(key, *languages):
        """Message d'erreur / d'information dans une ou plusieurs langues."""
        texts = [LABELS[lang][key] for lang in languages]
        return {
            'overview': ' / '.join(texts),
            'key_points': texts,
        }
 
 
def main():
    print("Document Summarizer / Resume de documents (FR / EN)")
    print("=" * 50)
 
    summarizer = UltimateSummarizer()
 
    while True:
        file_path = input("\nChemin du fichier / File path (ou 'quitter' / 'quit'): ").strip().strip('"')
        if file_path.lower() in ('quit', 'quitter', 'q'):
            break
 
        if not os.path.exists(file_path):
            print("Fichier non trouve / File not found.")
            continue
 
        result = summarizer.summarize_file(file_path)
        language = summarizer._detect_language(summarizer._extract_text(file_path)[:4000])
        print(summarizer.format_summary(result, language))
 
 
if __name__ == "__main__":
    main()
 