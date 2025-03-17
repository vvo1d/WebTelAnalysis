from flask import Flask, render_template, request, redirect, url_for
import json
import re
from collections import defaultdict, Counter
import networkx as nx
from flask_paginate import Pagination, get_page_parameter
import string
from pymorphy2 import MorphAnalyzer

app = Flask(__name__)

# Глобальные переменные для хранения данных чата
participants = {}
messages = []
chat_title = ''
administrators = []

# Инициализация MorphAnalyzer для нормализации слов
morph = MorphAnalyzer()

# Контекстный процессор для автоматической передачи переменных во все шаблоны
@app.context_processor
def inject_globals():
    return {
        'participants': participants if participants else None,
        'messages': messages if messages else None,
        'admins': administrators if administrators else None
    }

# Маршрут для главной страницы
@app.route('/')
def index():
    return render_template('index.html')

# Маршрут для загрузки JSON файла
@app.route('/upload', methods=['POST'])
def upload():
    global participants, messages, chat_title, administrators
    file = request.files['file']
    if file:
        try:
            chat = json.load(file)
        except json.JSONDecodeError:
            return "Ошибка: Неверный формат JSON", 400
        chat_title = chat.get('name', 'Unknown Chat')
        participants = extract_participants(chat)
        messages = chat['messages']
        administrators = extract_administrators(chat)
        return redirect(url_for('participants_list'))
    else:
        return "Файл не выбран", 400

# Функция для извлечения участников и количества их сообщений
def extract_participants(chat):
    participants_dict = {}
    messages = chat['messages']
    for message in messages:
        if 'from_id' in message:
            user_id = message['from_id']
            user_name = message.get('from') or 'Unknown'
            if user_id not in participants_dict:
                participants_dict[user_id] = {'name': user_name, 'message_count': 0}
            participants_dict[user_id]['message_count'] += 1
    return participants_dict

# Функция для определения администраторов
def extract_administrators(chat):
    admins = []
    for participant in chat.get('participants', []):
        role = participant.get('role', '').lower()
        if role in ['creator', 'administrator']:
            admins.append(participant['id'])
    return admins

# Маршрут для отображения списка участников
@app.route('/participants')
def participants_list():
    sort_by = request.args.get('sort_by', 'name')
    sorted_participants = list(participants.items())
    if sort_by == 'messages':
        sorted_participants.sort(key=lambda x: x[1]['message_count'], reverse=True)
    else:
        sorted_participants.sort(key=lambda x: x[1]['name'] or '')
    return render_template(
        'participants.html', 
        participants=sorted_participants, 
        admins=administrators, 
        chat_title=chat_title, 
        messages=messages
    )

# Маршрут для отображения сообщений конкретного пользователя
@app.route('/user/<user_id>')
def user_messages(user_id):
    user_msgs = [msg for msg in messages if msg.get('from_id') == user_id]
    user_name = participants.get(user_id, {}).get('name', 'Unknown')

    # Обрабатываем текст сообщений
    for msg in user_msgs:
        text = msg.get('text', '')
        text_content = ''
        if isinstance(text, str):
            text_content = text
        elif isinstance(text, list):
            for item in text:
                if isinstance(item, str):
                    text_content += item
                elif isinstance(item, dict):
                    text_content += item.get('text', '')
        msg['text_content'] = text_content
    return render_template('user_messages.html', messages=user_msgs, user_name=user_name, admins=administrators)

# Маршрут для поиска сообщений по ключевым словам
@app.route('/search')
def search_messages():
    keyword = request.args.get('keyword', '')
    if keyword:
        found_messages = []
        for msg in messages:
            text = msg.get('text', '')
            text_content = ''
            if isinstance(text, str):
                text_content = text
            elif isinstance(text, list):
                for item in text:
                    if isinstance(item, str):
                        text_content += item
                    elif isinstance(item, dict):
                        text_content += item.get('text', '')
            if keyword.lower() in text_content.lower():
                msg_copy = msg.copy()
                msg_copy['text_content'] = text_content
                found_messages.append(msg_copy)
        return render_template('search_results.html', messages=found_messages, keyword=keyword, participants=participants, admins=administrators)
    else:
        return redirect(url_for('index'))

# Маршрут для отображения графа связей
@app.route('/graph')
def display_graph():
    G = build_connection_graph()
    # Преобразуем граф в данные для vis.js
    graph_data = nx.node_link_data(G)
    
    # Обновляем узлы с метками имен
    for node in graph_data['nodes']:
        user_id = node['id']
        node['label'] = participants.get(user_id, {}).get('name', 'Unknown')
    
    # Обновляем ребра для vis.js
    for link in graph_data['links']:
        link['from'] = link.pop('source')
        link['to'] = link.pop('target')
    
    return render_template('graph.html', graph_data=graph_data)

def build_connection_graph():
    G = nx.DiGraph()
    for message in messages:
        sender_id = message.get('from_id')
        if not sender_id:
            continue
        # Добавляем отправителя в граф
        G.add_node(sender_id)
        # Проверяем наличие ответов
        if 'reply_to_message_id' in message:
            reply_to_id = message['reply_to_message_id']
            # Ищем оригинальное сообщение
            original_message = next((m for m in messages if m['id'] == reply_to_id), None)
            if original_message and 'from_id' in original_message:
                original_sender_id = original_message['from_id']
                # Добавляем ребро от отвечающего к оригинальному отправителю
                G.add_edge(sender_id, original_sender_id)
        # Проверяем наличие упоминаний
        text = message.get('text', '')
        if isinstance(text, list):
            text_content = ''.join([item.get('text', '') if isinstance(item, dict) else item for item in text])
        else:
            text_content = text
        mentions = re.findall(r'@(\w+)', text_content)
        for mention in mentions:
            # Ищем ID пользователя по никнейму
            mentioned_user_id = None
            for user in participants:
                if participants[user]['name'] == mention or user == mention:
                    mentioned_user_id = user
                    break
            if mentioned_user_id:
                G.add_edge(sender_id, mentioned_user_id)
    return G

# Маршрут для полной истории сообщений с пагинацией
@app.route('/history')
def message_history():
    page = request.args.get(get_page_parameter(), type=int, default=1)
    per_page = 50  # Количество сообщений на странице
    start = (page - 1) * per_page
    end = start + per_page
    total = len(messages)

    # Обрабатываем текст сообщений
    processed_messages = []
    for msg in messages[start:end]:
        text = msg.get('text', '')
        text_content = ''
        if isinstance(text, str):
            text_content = text
        elif isinstance(text, list):
            for item in text:
                if isinstance(item, str):
                    text_content += item
                elif isinstance(item, dict):
                    text_content += item.get('text', '')
        processed_messages.append({
            'date': msg.get('date', ''),
            'from_id': msg.get('from_id', ''),
            'from_name': participants.get(msg.get('from_id', ''), {}).get('name', 'Unknown'),
            'text_content': text_content
        })

    pagination = Pagination(page=page, total=total, per_page=per_page, css_framework='bootstrap4')

    return render_template('history.html',
                           messages=processed_messages,
                           pagination=pagination,
                           total=total,
                           admins=administrators)

# Маршрут для отображения всех ссылок из чата
@app.route('/links')
def links_list():
    # Регулярное выражение для поиска URL
    url_pattern = re.compile(
        r'(https?://(?:www\.|(?!www))[a-zA-Z0-9]+\.[^\s]{2,}|www\.[a-zA-Z0-9]+\.[^\s]{2,})'
    )
    links = []
    for msg in messages:
        text = msg.get('text', '')
        if isinstance(text, list):
            text_content = ''.join([item.get('text', '') if isinstance(item, dict) else item for item in text])
        else:
            text_content = text
        found_links = re.findall(url_pattern, text_content)
        for link in found_links:
            links.append({
                'link': link,
                'from_id': msg.get('from_id', ''),
                'from_name': participants.get(msg.get('from_id', ''), {}).get('name', 'Unknown')
            })
    return render_template('links.html', links=links, admins=administrators)

# Маршрут для отображения самых часто используемых слов
@app.route('/frequent_words')
def frequent_words():
    word_counter = Counter()
    for msg in messages:
        text = msg.get('text', '')
        if isinstance(text, list):
            text_content = ''.join([item.get('text', '') if isinstance(item, dict) else item for item in text])
        else:
            text_content = text
        # Удаление пунктуации и приведение к нижнему регистру
        translator = str.maketrans('', '', string.punctuation + '«»—…“”')
        clean_text = text_content.translate(translator).lower()
        # Разделение на слова
        words = clean_text.split()
        # Исключение стоп-слов (можно добавить или изменить список)
        stop_words = set([
            'и', 'в', 'во', 'не', 'что', 'он', 'на', 'я', 'с', 'со', 'как', 'а', 'то', 
            'все', 'она', 'так', 'его', 'но', 'да', 'ты', 'к', 'у', 'же', 'вы', 'за', 
            'бы', 'по', 'только', 'ее', 'мне', 'было', 'вот', 'от', 'меня', 'еще', 
            'нет', 'о', 'из', 'ему', 'теперь', 'когда', 'даже', 'ну', 'вдруг', 'ли', 
            'если', 'уже', 'или', 'ни', 'быть', 'был', 'него', 'до', 'вас', 'нибудь', 
            'опять', 'уж', 'вам', 'ведь', 'там', 'потом', 'себя', 'ничего', 'ей', 
            'может', 'они', 'тут', 'где', 'есть', 'надо', 'ней', 'для', 'мы', 'тебя', 
            'их', 'чем', 'была', 'сам', 'чтоб', 'без', 'будто', 'чего', 'раз', 'тоже', 
            'себе', 'под', 'будет'
        ])
        # Нормализация слов
        normalized_words = [morph.parse(word)[0].normal_form for word in words if word not in stop_words]
        word_counter.update(normalized_words)

    # Получение 20 самых частых слов
    most_common = word_counter.most_common(20)

    return render_template('frequent_words.html', most_common=most_common)

if __name__ == '__main__':
    app.run(host='localhost', port=9001)
