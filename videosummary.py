import argparse
import html
import os
import re
import sys
from collections import Counter
from datetime import timedelta
    
try:
    import requests
except ImportError:  
    requests = None
 
try:
    from pytube import YouTube
except ImportError:  
    try:
        from pytubefix import YouTube
    except ImportError:
        YouTube = None
 
from youtube_transcript_api import YouTubeTranscriptApi
 
from file_summary import UltimateSummarizer, WORD_RE
 
_TOOLS = UltimateSummarizer()   
 
VIDEO_LABELS = {
    'french': {
        'intro': "La video « {title} »{author}{duration} traite principalement de {topics}.",
        'by': " de {author}",
        'lasting': " (duree : {duration})",
        'and': "et",
        'no_transcript': "Aucune transcription disponible pour le resume.",
        'title_overview': "RESUME DE LA VIDEO",
        'title_points': "POINTS CLES",
    },
    'english': {
        'intro': "The video \"{title}\"{author}{duration} mainly covers {topics}.",
        'by': " by {author}",
        'lasting': " (duration: {duration})",
        'and': "and",
        'no_transcript': "No transcript available for the summary.",
        'title_overview': "VIDEO SUMMARY",
        'title_points': "KEY POINTS",
    },
}
 

FILLER_WORDS = set("""
    um uh uhm like yeah okay ok gonna wanna just really actually basically
    euh ben bah quoi voila bon hein ouais
""".split())
 
 

def format_time(seconds):
    """Convertit des secondes en [M:SS] (ou [H:MM:SS] pour les longues videos)."""
    seconds = int(seconds)
    if seconds >= 3600:
        return f"[{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}]"
    return f"[{seconds // 60}:{seconds % 60:02d}]"
 
 
def extract_video_id(url):
    """Extrait l'ID YouTube (11 caracteres) d'une URL : watch, youtu.be, embed, shorts, live."""
    url = (url or '').strip()
    if re.fullmatch(r'[A-Za-z0-9_-]{11}', url):
        return url
    match = re.search(
        r'(?:[?&]v=|youtu\.be/|/embed/|/shorts/|/live/|/v/)([A-Za-z0-9_-]{11})', url
    )
    if match:
        return match.group(1)
    raise ValueError("Invalid YouTube URL / URL YouTube invalide")
 
 
def get_video_info(video_id):
    """Recupere titre, chaine, duree... avec plusieurs methodes de secours."""
   
    if YouTube is not None:
        try:
            yt = YouTube(f"https://youtube.com/watch?v={video_id}")
            return {
                "title": yt.title,
                "author": yt.author,
                "length": yt.length,
                "views": yt.views,
                "publish_date": yt.publish_date.strftime('%Y-%m-%d') if yt.publish_date else "Unknown",
            }
        except Exception:
            pass
 
    
    if requests is not None:
        try:
            response = requests.get(
                "https://www.youtube.com/oembed",
                params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
                timeout=10,
            )
            data = response.json()
            return {
                "title": data.get('title', f"Video {video_id}"),
                "author": data.get('author_name', 'Unknown'),
                "length": 0,
                "views": "Unknown",
                "publish_date": "Unknown",
            }
        except Exception:
            pass
 
    
    return {
        "title": f"Video {video_id}",
        "author": "Unknown",
        "length": 0,
        "views": "Unknown",
        "publish_date": "Unknown",
    }
 
 
def _short_error(exc):
    """Message d'erreur court (les erreurs de la librairie sont tres longues)."""
    lines = [l.strip() for l in str(exc).strip().splitlines() if l.strip()]
    message = ' '.join(lines[:3])[:300]
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
 
 
def get_transcript(video_id, languages=('fr', 'en')):
    """Recupere la transcription [(texte, secondes), ...].
 
    Essaie d'abord le francais puis l'anglais, puis n'importe quelle langue
    disponible. Leve une erreur claire si aucune transcription n'est trouvee.
    """
    try:
        raw = _fetch_transcript(video_id, list(languages))
    except Exception as e:
        raise RuntimeError(
            "Impossible de recuperer les sous-titres / Could not get the transcript. "
            + _short_error(e)
        )
 
    transcript = [(t.strip(), s) for t, s in raw if t and t.strip()]
    if not transcript:
        raise RuntimeError("Aucune transcription disponible / No transcript available")
    return transcript
 
 
def _fetch_transcript(video_id, languages):
    """Compatible avec l'ancienne (<1.0) et la nouvelle API de youtube-transcript-api."""
    
    if not hasattr(YouTubeTranscriptApi, 'get_transcript'):
        api = YouTubeTranscriptApi()
        try:
            fetched = api.fetch(video_id, languages=languages)
        except Exception:
            transcripts = api.list(video_id)
            fetched = next(iter(transcripts)).fetch()   # premiere langue disponible
        return [(snippet.text, snippet.start) for snippet in fetched]
 
    
    try:
        items = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
    except Exception:
        transcripts = YouTubeTranscriptApi.list_transcripts(video_id)
        items = next(iter(transcripts)).fetch()
    return [(item['text'], item['start']) for item in items]
 
 
def save_transcript_by_minutes(transcript, output_dir, filename):
    """Enregistre la transcription avec horodatage, groupee par minute."""
    if not transcript:
        return
 
    output_path = os.path.join(output_dir, filename)
 
    by_minute = {}
    for text, timestamp in transcript:
        by_minute.setdefault(int(timestamp // 60), []).append((text, timestamp))
 
    with open(output_path, 'w', encoding='utf-8') as f:
        for minute in sorted(by_minute):
            for text, timestamp in by_minute[minute]:
                f.write(f"{format_time(timestamp)} {text}\n")
            f.write("\n")
 
 

def _unpack(entry):
    """Accepte (texte, debut) ou {'text':..., 'start':...}."""
    if isinstance(entry, dict):
        return entry.get('text', ''), entry.get('start', 0)
    return entry[0], entry[1]
 
 
def _clean_caption(text):
    """Nettoie un sous-titre : [Music], >>, retours a la ligne, entites HTML."""
    text = html.unescape(text or '')
    text = re.sub(r'\[[^\]]*\]', ' ', text)          # [Music], [Applause]...
    text = text.replace('\u266A', ' ').replace('>>', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    return text
 
 
def _tidy(sentence):
    sentence = sentence.strip()
    if not sentence:
        return ''
    sentence = sentence[0].upper() + sentence[1:]
    if sentence[-1] not in '.!?\u2026':
        sentence += '.'
    return sentence
 
 
def build_segments(transcript, target_words=24):
    """Regroupe les petits sous-titres en passages [(texte, debut_en_secondes)]."""
    segments = []
    buffer = []
    buffer_start = 0
    words = 0
 
    for entry in transcript:
        text, start = _unpack(entry)
        text = _clean_caption(text)
        if not text:
            continue
        if not buffer:
            buffer_start = start
        buffer.append(text)
        words += len(text.split())
        ends_sentence = text.endswith(('.', '!', '?'))
        if words >= target_words or (ends_sentence and words >= 12):
            segments.append((_tidy(' '.join(buffer)), buffer_start))
            buffer, words = [], 0
 
    if buffer:
        segments.append((_tidy(' '.join(buffer)), buffer_start))
    return segments
 
 
def _top_topics(segments, stop, count=5):
    """Mots-cles les plus frequents (formes affichables)."""
    freq = Counter()
    forms = {}
    for text, _ in segments:
        for raw in WORD_RE.findall(text):
            low = raw.lower()
            if len(low) < 3 or low in stop or low in FILLER_WORDS:
                continue
            norm = _TOOLS._normalize(low)
            freq[norm] += 1
            forms.setdefault(norm, Counter())[low] += 1
    topics = [forms[w].most_common(1)[0][0] for w, _ in freq.most_common(count)]
    return topics, freq
 
 
def generate_summary(video_info, transcript, num_points=8, language=None):
    """Genere le resume : {'overview': str, 'key_points': ['[M:SS] phrase', ...]}."""
    if not transcript:
        return {
            'overview': VIDEO_LABELS['french']['no_transcript'] + " / " +
                        VIDEO_LABELS['english']['no_transcript'],
            'key_points': [],
        }
 
    segments = build_segments(transcript)
    if not segments:
        return {
            'overview': VIDEO_LABELS['french']['no_transcript'] + " / " +
                        VIDEO_LABELS['english']['no_transcript'],
            'key_points': [],
        }
 
    full_text = ' '.join(t for t, _ in segments)
    if language not in VIDEO_LABELS:
        language = _TOOLS._detect_language(full_text)
    labels = VIDEO_LABELS[language]
    stop = _TOOLS._get_stopwords(language) | FILLER_WORDS
 
    topics, freq = _top_topics(segments, stop)
 
    
    author = video_info.get('author')
    author_part = labels['by'].format(author=author) if author and author != 'Unknown' else ''
    length = video_info.get('length')
    duration_part = ''
    if isinstance(length, (int, float)) and length > 0:
        duration_part = labels['lasting'].format(
            duration=str(timedelta(seconds=int(length)))
        )
    title = (video_info.get('title') or '').strip()
    intro = labels['intro'].format(
        title=title, author=author_part, duration=duration_part,
        topics=_TOOLS._join_list(topics, labels['and']) if topics else '...',
    )
 
    
    if len(segments) <= 3 or not freq:
        return {
            'overview': intro + "\n\n" + ' '.join(t for t, _ in segments),
            'key_points': [f"{format_time(s)} {t}" for t, s in segments],
        }
 
    
    max_freq = max(freq.values())
    weight = {w: c / max_freq for w, c in freq.items()}
 
    tokens = []
    for text, _ in segments:
        toks = []
        for raw in WORD_RE.findall(text):
            low = raw.lower()
            if len(low) < 3 or low in stop:
                continue
            toks.append(_TOOLS._normalize(low))
        tokens.append(toks)
 
    total = len(segments)
    scores = {}
    for i, (text, _) in enumerate(segments):
        toks = tokens[i]
        if len(text.split()) < 6 or not toks:
            continue
        base = sum(weight.get(t, 0) for t in toks) / (len(toks) ** 0.6)
        position = i / total
        bonus = 0.15 if (position < 0.1 or position > 0.9) else 0.0 
        scores[i] = base + bonus
 
    if not scores:
        return {
            'overview': intro,
            'key_points': [f"{format_time(s)} {t}" for t, s in segments[:num_points]],
        }
 
    ranked = sorted(scores, key=lambda i: scores[i], reverse=True)
 
    def pick(count, min_gap):
        chosen = []
        for i in ranked:
            toks_i = set(tokens[i])
            if any(_TOOLS._jaccard(toks_i, set(tokens[j])) > 0.5 for j in chosen):
                continue
            if any(abs(segments[i][1] - segments[j][1]) < min_gap for j in chosen):
                continue
            chosen.append(i)
            if len(chosen) >= count:
                break
        return chosen
 
    num_points = max(1, int(num_points))
    key_idx = pick(num_points, min_gap=20)          
    if len(key_idx) < min(num_points, len(scores)):
        key_idx = pick(num_points, min_gap=0)       
 
    
    overview_count = min(5, len(key_idx))
    overview_idx = sorted(key_idx[:overview_count])  
    overview = intro + "\n\n" + ' '.join(segments[i][0] for i in overview_idx)
 
    key_points = [
        f"{format_time(segments[i][1])} {_TOOLS._truncate(segments[i][0], 45)}"
        for i in sorted(key_idx, key=lambda i: segments[i][1])
    ]
 
    return {'overview': overview, 'key_points': key_points}
 
 
def format_summary(summary, language=None):
    """Met en forme le resume (dict ou texte) pour la console / un fichier."""
    if isinstance(summary, str):
        return summary
    if language not in VIDEO_LABELS:
        language = _TOOLS._detect_language(summary['overview'])
    labels = VIDEO_LABELS[language]
    bar = '-' * 60
    points = '\n'.join('- ' + p for p in summary['key_points'])
    return (
        labels['title_overview'] + "\n" + bar + "\n" + summary['overview'] + "\n\n" +
        labels['title_points'] + "\n" + bar + "\n" + points + "\n"
    )
 
 

def save_output(video_info, transcript, summary, output_dir="output"):
    """Enregistre infos, transcription et resume dans un dossier."""
    os.makedirs(output_dir, exist_ok=True)
 
    safe_title = re.sub(r'[^\w\s-]', '', video_info['title']).strip().replace(' ', '_')
    if not safe_title:
        safe_title = f"video_{video_info.get('id', 'summary')}"
 
    info_file = os.path.join(output_dir, f"{safe_title}_info.txt")
    with open(info_file, 'w', encoding='utf-8') as f:
        f.write(f"Title: {video_info['title']}\n")
        f.write(f"Channel: {video_info['author']}\n")
        length = video_info['length']
        if isinstance(length, (int, float)):
            f.write(f"Length: {str(timedelta(seconds=int(length)))}\n")
        else:
            f.write(f"Length: {length}\n")
        f.write(f"Views: {video_info['views']}\n")
        f.write(f"Published: {video_info['publish_date']}\n")
 
    transcript_name = f"{safe_title}_transcript.txt"
    save_transcript_by_minutes(transcript, output_dir, transcript_name)
 
    summary_file = os.path.join(output_dir, f"{safe_title}_summary.txt")
    with open(summary_file, 'w', encoding='utf-8') as f:
        f.write(format_summary(summary))
 
    print(f"\nFichiers enregistres dans {output_dir}/ :")
    print(f"- Infos : {info_file}")
    print(f"- Transcription : {os.path.join(output_dir, transcript_name)}")
    print(f"- Resume : {summary_file}")
 
 
def main():
    parser = argparse.ArgumentParser(description="Extract and summarize YouTube video content")
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument("-o", "--output", default="output", help="Output directory (default: 'output')")
    parser.add_argument("--no-summary", action="store_true", help="Skip generating summary")
    parser.add_argument("-s", "--sentences", type=int, default=8,
                        help="Number of key points in summary (default: 8)")
    args = parser.parse_args()
 
    try:
        print(f"Processing video: {args.url}")
 
        video_id = extract_video_id(args.url)
        print(f"Video ID: {video_id}")
 
        print("Extracting video information...")
        video_info = get_video_info(video_id)
        video_info['id'] = video_id
        print(f"Title: {video_info['title']}")
        print(f"Channel: {video_info['author']}")
 
        print("Extracting transcript...")
        transcript = get_transcript(video_id)
 
        if args.no_summary:
            summary = {'overview': "Summary generation was skipped.", 'key_points': []}
        else:
            print("Generating summary...")
            summary = generate_summary(video_info, transcript, num_points=args.sentences)
 
        save_output(video_info, transcript, summary, args.output)
 
        print("\n" + "-" * 80)
        print(format_summary(summary))
        print("-" * 80)
        print("Done!")
 
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
 
 
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python videosummary.py <video_url>")
        sys.exit(1)
    main()
 