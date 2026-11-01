import nltk
import numpy as np
from nltk.corpus import stopwords
from nltk.tokenize import sent_tokenize, word_tokenize
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from collections import Counter


nltk.download('punkt', quiet=True)
nltk.download('stopwords', quiet=True)

def preprocess_text(text):
    """
    Preprocess the text by tokenizing and removing stopwords.
    """
    sentences = sent_tokenize(text)
    stop_words = set(stopwords.words('english'))
    processed_sentences = []
    
    for sentence in sentences:
        words = word_tokenize(sentence.lower())
        words = [word for word in words if word.isalnum() and word not in stop_words]
        processed_sentences.append(' '.join(words))
    
    return sentences, processed_sentences

def extract_keywords(text, top_n=10):
    """
    Extract top keywords from the text using TF-IDF.
    """
    vectorizer = TfidfVectorizer(stop_words='english')
    tfidf_matrix = vectorizer.fit_transform([text])
    feature_names = vectorizer.get_feature_names_out()
    tfidf_scores = tfidf_matrix.toarray()[0]
    
    
    sorted_indices = np.argsort(tfidf_scores)[::-1]
    keywords = [feature_names[i] for i in sorted_indices[:top_n]]
    return keywords

def score_sentences(sentences, keywords):
    """
    Score sentences based on the presence of keywords.
    """
    sentence_scores = np.zeros(len(sentences))
    
    for idx, sentence in enumerate(sentences):
        words = word_tokenize(sentence.lower())
        score = sum(1 for word in words if word in keywords)
        sentence_scores[idx] = score
    
    return sentence_scores

def summarize_text(text, num_sentences=2):
    """
    Improved summarization using TF-IDF on all sentences.
    """
    
    sentences = sent_tokenize(text)
    
    
    if len(sentences) <= num_sentences:
        return text
    
    
    vectorizer = TfidfVectorizer(stop_words='english')
    tfidf_matrix = vectorizer.fit_transform(sentences)
    sentence_scores = np.array(tfidf_matrix.sum(axis=1)).flatten()
    
   
    top_indices = np.argsort(sentence_scores)[-num_sentences:]
    top_indices.sort() 
    
    
    summary = ' '.join([sentences[i] for i in top_indices])
    return summary