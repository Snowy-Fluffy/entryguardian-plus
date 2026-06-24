



import json

class Translator:
	locale = dict()
	
	def __init__(self, lc_code):
		with open(f'l10n/{lc_code}.json') as file:
			self.locale = json.load(file)
	
	def get_string(self, string_id):
		return self.locale.get(string_id, '<no_such_string>')

