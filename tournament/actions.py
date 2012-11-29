from google.appengine.ext import db

from auth import auth_models

import models
import logging
import math
import json
import jsonpickle

def get_tournaments_by_user(user_key):
    tournaments = models.Tournament.all().filter('owner =', user_key).filter('order', 1).fetch(1000)
    tournaments.extend(models.Tournament.all().filter('admins', user_key).filter('order', 1).fetch(1000))
    t_set = set(tournaments)
    return t_set

def get_tournament_by_id(id):
	return models.Tournament.get_by_id(id)

def get_user_by_id(id):
    return auth_models.WTUser.get_by_id(id)

def get_user_by_email(email):
    user = auth_models.WTUser.all().filter('email =', email).fetch(1)
    if user:
        return user[0]
    return None

def get_linked_tournaments(tournament):
    linked = []
    t = tournament
    while True:
        if t.next_tournament:
            linked.append(t.next_tournament)
            t = t.next_tournament
        else:
            break
    return linked

def get_matches_by_tournament(tournament, limit = 200):
    matches = models.Match.all().ancestor(tournament).fetch(limit)
    return matches

def get_top_match_by_tournament(tournament):
    matches = models.Match.all().ancestor(tournament).fetch(limit=200)
    for match in matches:
        if not match.next_match:
            return match
    return None

def get_participants_by_match(match, limit = 200):
    participants = models.Participant.all().ancestor(match).fetch(limit)
    return participants

def get_json_by_tournament(tournament):
    top_match = get_top_match_by_tournament(tournament)
    pickled = json.dumps(top_match, cls=models.MatchEncoder)
    tournament_json= {"title":tournament.name, "type":tournament.type,"created":tournament.created,"date":tournament.date
    ,"location":tournament.location,"matches":pickled}
    encoded = "{\"title\":\""+tournament.name+"\",\"type\":\""+tournament.type+"\",\"matches\":"+pickled+"}"
    return encoded

# Can participants have different parents?
def write_player_to_db(player, cur_match):
    if player is not None and player['seed'] is not None and player['name'] is not None:
        p1 = models.Participant(
            seed=player['seed'],
            name=player['name'],
            parent=cur_match)
        cur_match.put()

#TODO: Update the winner and add to the next participants
def update_match_by_winner(match_id, winner):
    # Get the selected match by id
    match = models.Match.get_by_id(match_id)
    participants = get_participants_by_match(match)
    # If there is a participant, then change the match status to played.
    # Add the winner to participants of the next match
    if participants.__contains__(winner):
        match.has_been_played = True
        write_player_to_db(winner, match.next_match)
    # Reload the view page
    return None

def create_tournament(form_data, p_form_data, user):
    t = models.Tournament(
        name=form_data.get('name'),
        date=form_data.get('date'),
        location=form_data.get('location'),
        owner=user,
        perms=form_data.get('tournament_security'),
        type=form_data.get('type'),
        order=1,
        win_method=models.Tournament.HIGHEST_WINS)
    t.put()

    seeded_list = []
    # Fill the seed data from forms
    if form_data.get('show_seeds', False):
        name_dict = {}
        seeds_dict = {}

        for field, value in p_form_data.items():
            if 'seed' in field:
                num = field[11:-4]
                seeds_dict[int(num)] = value
            if 'name' in field:
                num = field[11:-4]
                name_dict[int(num)] = value

        seeded_list = [{'name':None,'seed':None}]*len(seeds_dict)
        for i in range(len(seeds_dict)):
            seed_index = seeds_dict[i]
            if seed_index is not None:
                seeded_list[seed_index-1] = {'name':name_dict[i],'seed':seeds_dict[i]}
    else:
        for field, value in p_form_data.items():
            if 'name' in field:
                num = field[11:-4]
                seeded_list.append({'name':value,'seed':int(num)+1})


    # I couldn't fit this into the build tourney recursion, however this helps decides the round number for
    # each match. This method associates each round number with the level of recursion.
    # for example a tourney of 16 people
    # 0:[15] 
    # 1:[14, 13] 
    # 2:[12, 11, 10, 9] 
    # 3:[8, 7, 6, 5, 4, 3, 2, 1]
    # so each each time we create a new match we just pop an element off of the list depedning on our level of
    # recursion
    # Generate the tournament brackets based on number: the total number of games for single elimination  
    def decide_rounds(dict_to_fill, num_of_rounds, level=0):
        if num_of_rounds <=0:
            return
        step = num_of_rounds-int(math.pow(2,level))
        if step<0: 
            step=0
        dict_to_fill[level] = [i for i in range(num_of_rounds, step, -1)]
        decide_rounds(dict_to_fill,step,level+1)

    round_dict = {}
    decide_rounds(round_dict,len(seeded_list)-1)
    logging.info("round_dict: %s", round_dict)
    ps_to_put = []

    def build_matches_helper(next_match, is_odd, level=0, round =1):


        logging.info('level: %d, next_match: %s,   %s', level, next_match, round_dict[level])
        def write_player(player, cur_match):
            if player is not None and player['seed'] is not None and player['name'] is not None:
                p1 = models.Participant(
                    seed=player['seed'],
                    name=player['name'],
                    parent=cur_match)
                logging.info('player: %s is added to %d', p1.name, cur_match.key().id())
                ps_to_put.append(p1)

        m = models.Match(round=round, has_been_played=False, parent=t, next_match = next_match)
        m.put()
        logging.info("match %d is generated", m.key().id())

        if next_match:
            logging.info("add children %s to %s", m.key().id(), next_match.key().id())
            next_match.add_children_match(m)

        is_leaf=True
        if round_dict.has_key(level+1):
            candidates = []
            if len(round_dict[level+1])>1:
                candidates.append(round_dict[level+1].pop())
                candidates.append(round_dict[level+1].pop())
            elif len(round_dict[level+1])>0:
                candidates.append(round_dict[level+1].pop())
            while len(candidates)>0:
                logging.info(candidates)
                is_leaf = False
                build_matches_helper(m, is_odd, level+1, round+1)
                i = candidates.pop()
                logging.info('remove: %d', i)
                if round_dict.has_key(level+1) and is_odd:
                    if len(candidates)==0 and len(seeded_list)>0:
                        is_odd = False
                        write_player(seeded_list.pop(),m)
            
        if is_leaf:
            write_player(seeded_list.pop(),m)
            write_player(seeded_list.pop(),m)
        
    
    is_odd = False
    if len(seeded_list)%2 == 1:
        is_odd = True
    build_matches_helper(None, is_odd)
    db.put(ps_to_put)
