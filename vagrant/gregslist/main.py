from flask import Flask, render_template, request, redirect, jsonify, url_for, flash
from flask import make_response
from sqlalchemy import create_engine, asc
from sqlalchemy.orm import sessionmaker
from database_setup import Base, User, JobCategory, StuffCategory, SpaceCategory, JobPost, StuffPost, SpacePost
from flask import session as login_session
import random
import string
from oauth2client.client import flow_from_clientsecrets
from oauth2client.client import FlowExchangeError
import httplib2
import json
import requests
from functools import wraps

# https://console.developers.google.com/apis/credentials?project=greglist-174419
GOOGLE_CLIENT_ID = json.loads(
	open('client_secret.json', 'r').read())['web']['client_id']

# https://developers.facebook.com/apps/555177401540603/settings/
FACEBOOK_APP_ID = json.loads(
	open('fb_client_secrets.json', 'r').read())['web']['app_id']

# decorators:
# http://exploreflask.com/en/latest/views.html
# @login_required

# Connect to Database and create database session
engine = create_engine('sqlite:///gregslist.db')
Base.metadata.bind = engine
DBSession = sessionmaker(bind=engine)
session = DBSession()

app = Flask(__name__)

def login_required(func):
	"""
	A decorator to confirm login or redirect as needed
	"""
	@wraps(func)
	def wrap(*args, **kwargs):
		print args
		print kwargs
		if 'logged_in' in login_session:
			return func(*args, **kwargs)
		else:
			flash("[warning]You need to login first")
			print "Will REDIRECT " + login_session['current_url']
			return redirect(login_session['current_url'])
	return wrap

def owner_filter(func):
	"""
	A decorator to confirm if user created the item
	and display so page can 'edit'/'delete' buttons as needed
	"""
	@wraps(func)
	def wrap(*args, **kwargs):
		# if all tests fail, current user is not owner
		kwargs['is_owner'] = False
		if 'post_id' in kwargs:
			try:
				post = session.query(JobPost).filter_by(id=kwargs['post_id']).one()
				kwargs["post"] = post
			except:
				flash("[warning]This post does not exist")
				print "Will REDIRECT " + login_session['current_url']
				return redirect(login_session['current_url'])
			if 'user_id' in login_session and post.user_id == login_session['user_id']:
					# current user created this post
					kwargs["is_owner"] = True
					return func(*args, **kwargs)
		return func(*args, **kwargs)
	return wrap

def ownership_required(func):
	"""
	A decorator to confirm authorization and redirect as needed.
	Intended to be used directly after owner_filter.
	"""
	@wraps(func)
	def wrap(*args, **kwargs):
		if 'is_owner' in kwargs and 'post' in kwargs:
			if kwargs['is_owner']:
				del kwargs['is_owner']
				return func(*args, **kwargs)
		flash("[warning]You do not own this post")
		print "Will REDIRECT " + login_session['current_url']
		return redirect(login_session['current_url'])
	return wrap


@app.route('/gregslist/login/')
def showLogin():
	state = ''.join(random.choice(string.ascii_uppercase + string.digits)
					for x in xrange(32))
	login_session['state'] = state
	# return "The current session state is %s" % login_session['state']
	return render_template('login.html', STATE=state,
							GOOGLE_CLIENT_ID=GOOGLE_CLIENT_ID,
							FACEBOOK_APP_ID=FACEBOOK_APP_ID)


@app.route('/gregslist/gconnect', methods=['POST'])
def gconnect():
	# Validate state token
	if request.args.get('state') != login_session['state']:
		response = make_response(json.dumps('Invalid state parameter.'), 401)
		response.headers['Content-Type'] = 'application/json'
		return response
	# Obtain authorization code
	code = request.data

	try:
		# Upgrade the authorization code into a credentials object
		oauth_flow = flow_from_clientsecrets('client_secret.json', scope='')
		oauth_flow.redirect_uri = 'postmessage'
		credentials = oauth_flow.step2_exchange(code)
	except FlowExchangeError:
		response = make_response(
			json.dumps('Failed to upgrade the authorization code.'), 401)
		response.headers['Content-Type'] = 'application/json'
		return response

	# Check that the access token is valid.
	access_token = credentials.access_token
	url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=%s'
		   % access_token)
	h = httplib2.Http()
	result = json.loads(h.request(url, 'GET')[1])
	# If there was an error in the access token info, abort.
	if result.get('error') is not None:
		response = make_response(json.dumps(result.get('error')), 500)
		response.headers['Content-Type'] = 'application/json'
		return response

	# Verify that the access token is used for the intended user.
	gplus_id = credentials.id_token['sub']
	if result['user_id'] != gplus_id:
		response = make_response(
			json.dumps("Token's user ID doesn't match given user ID."), 401)
		response.headers['Content-Type'] = 'application/json'
		return response

	# Verify that the access token is valid for this app.
	if result['issued_to'] != GOOGLE_CLIENT_ID:
		response = make_response(
			json.dumps("Token's client ID does not match app's."), 401)
		print "Token's client ID does not match app's."
		response.headers['Content-Type'] = 'application/json'
		return response

	stored_access_token = login_session.get('access_token')
	stored_gplus_id = login_session.get('gplus_id')
	if stored_access_token is not None and gplus_id == stored_gplus_id:
		response = make_response(json.dumps('Current user is already connected.'),
								 200)
		response.headers['Content-Type'] = 'application/json'
		return response

	# Store the access token in the session for later use.
	login_session['access_token'] = credentials.access_token
	login_session['gplus_id'] = gplus_id

	# Get user info
	userinfo_url = "https://www.googleapis.com/oauth2/v1/userinfo"
	params = {'access_token': credentials.access_token, 'alt': 'json'}
	answer = requests.get(userinfo_url, params=params)

	data = answer.json()

	login_session['logged_in'] = True
	login_session['provider'] = 'google'
	login_session['username'] = data['name']
	login_session['picture'] = data['picture']
	login_session['email'] = data['email']


	user_id = getUserID(login_session['email'])
	if not user_id:
		user_id = createUser(login_session)
	login_session['user_id'] = user_id
	flash("[success]you are now logged in as %s" % login_session['username'])
	return render_template('login-success.html',
							username=login_session['email'],
							img_url=login_session['picture'])


@app.route('/gregslist/gdisconnect/')
@login_required
def gdisconnect():
	access_token = None
	if 'access_token' in login_session:
		access_token = login_session['access_token']
	if access_token is None:
		print 'Access Token is None'
		response = make_response(json.dumps(
			'Current user not connected.'), 401)
		response.headers['Content-Type'] = 'application/json'
		return response
	url = 'https://accounts.google.com/o/oauth2/revoke?token=%s' % login_session[
		'access_token']
	h = httplib2.Http()
	result = h.request(url, 'GET')[0]
	print 'result is '
	print result
	if result['status'] == '200':
		del login_session['logged_in']
		del login_session['access_token']
		del login_session['gplus_id']
		del login_session['username']
		del login_session['email']
		del login_session['picture']
		del login_session['provider']
		response = make_response(json.dumps('Successfully disconnected.'), 200)
		response.headers['Content-Type'] = 'application/json'
		return response
	else:
		response = make_response(json.dumps(
			'Failed to revoke token for given user.', 400))
		response.headers['Content-Type'] = 'application/json'
		return response

# Facebook login
@app.route('/gregslist/fbconnect', methods=['POST'])
def fbconnect():
	if request.args.get('state') != login_session['state']:
		response = make_response(json.dumps('Invalid state parameter.'), 401)
		response.headers['Content-Type'] = 'application/json'
		return response
	access_token = request.data
	print "access token received %s " % access_token


	app_id = json.loads(open('fb_client_secrets.json', 'r').read())[
		'web']['app_id']
	app_secret = json.loads(
		open('fb_client_secrets.json', 'r').read())['web']['app_secret']
	url = 'https://graph.facebook.com/oauth/access_token?grant_type=fb_exchange_token&client_id=%s&client_secret=%s&fb_exchange_token=%s' % (
		app_id, app_secret, access_token)
	h = httplib2.Http()
	result = h.request(url, 'GET')[1]


	# Use token to get user info from API
	userinfo_url = "https://graph.facebook.com/v2.8/me"
	'''
		Due to the formatting for the result from the server token exchange we have to
		split the token first on commas and select the first index which gives us the key : value
		for the server access token then we split it on colons to pull out the actual token value
		and replace the remaining quotes with nothing so that it can be used directly in the graph
		api calls
	'''
	token = result.split(',')[0].split(':')[1].replace('"', '')

	url = 'https://graph.facebook.com/v2.8/me?access_token=%s&fields=name,id,email' % token
	h = httplib2.Http()
	result = h.request(url, 'GET')[1]
	# print "url sent for API access:%s"% url
	# print "API JSON result: %s" % result
	data = json.loads(result)
	login_session['logged_in'] = True
	login_session['provider'] = 'facebook'
	login_session['username'] = data["name"]
	login_session['email'] = data["email"]
	login_session['facebook_id'] = data["id"]

	# The token must be stored in the login_session in order to properly logout
	login_session['access_token'] = token

	# Get user picture
	url = 'https://graph.facebook.com/v2.8/me/picture?access_token=%s&redirect=0&height=200&width=200' % token
	h = httplib2.Http()
	result = h.request(url, 'GET')[1]
	data = json.loads(result)

	login_session['picture'] = data["data"]["url"]

	# see if user exists
	user_id = getUserID(login_session['email'])
	if not user_id:
		user_id = createUser(login_session)
	login_session['user_id'] = user_id

	flash("[success]Now logged in as %s" % login_session['username'])

	return render_template('login-success.html',
							username=login_session['username'],
							img_url=login_session['picture'])


# fb logout
@app.route('/gregslist/fbdisconnect/')
@login_required
def fbdisconnect():
	facebook_id = login_session['facebook_id']
	# The access token must me included to successfully logout
	access_token = login_session['access_token']
	url = 'https://graph.facebook.com/%s/permissions?access_token=%s' % (facebook_id,access_token)
	h = httplib2.Http()
	result = h.request(url, 'DELETE')[1]
	if result == '{"success":true}':
		del login_session['logged_in']
		del login_session['user_id']
		del login_session['provider']
		del login_session['username']
		del login_session['email']
		del login_session['facebook_id']
		return "you have been logged out"

# Show all posts
@app.route('/')
@app.route('/gregslist/')
def mainPage():
	login_session['current_url'] = request.url
	job_categories = jobCategories(pagefunc='showJobCategory')
	return render_template('index.html', job_categories=job_categories)

@app.route('/gregslist/<int:category_id>/job-category/')
def showJobCategory(category_id):
	login_session['current_url'] = request.url
	job_categories = jobCategories(pagefunc='showJobCategory',
								   mini=True,
								   highlight=category_id)
	job_posts = session.query(JobPost).filter_by(category_id=category_id).order_by(JobPost.title)
	return render_template('specific-category.html', job_categories=job_categories, posts=job_posts)


@app.route('/gregslist/<int:post_id>/post/')
@owner_filter
def showJobPost(post_id, post, is_owner):
	login_session['current_url'] = request.url
	return render_template('specific-item.html', post=post, is_owner=is_owner)

@app.route('/gregslist/<int:post_id>/delete/', methods=['GET', 'POST'])
@login_required
@owner_filter
@ownership_required
def deletePost(post_id, post):
	login_session['current_url'] = request.url
	if request.method == 'POST':
		category_id = post.category_id
		session.delete(post)
		flash('[info]"%s" has been deleted' % post.title)
		session.commit()
		return redirect(url_for('showJobCategory', category_id=category_id))
	else:
		return render_template('delete-item.html', post=post)

@app.route('/gregslist/<int:post_id>/edit/', methods=['GET', 'POST'])
@login_required
@owner_filter
@ownership_required
def editPost(post_id, post):
	login_session['current_url'] = request.url
	if request.method == 'POST':
		post.title = request.form['title']
		post.description = request.form['description']
		flash('[success]"%s" successfully edited' % post.title)
		session.commit()
		return redirect(url_for('showJobPost', post_id=post_id))
	else:
		return render_template('create-or-edit.html',
								title=post.title,
								description=post.description)

@app.route('/gregslist/choose/category/', methods=['GET', 'POST'])
@login_required
def newPostCategorySelect():
	login_session['current_url'] = request.url
	if request.method == 'POST':
		if 'jobs' in request.form:
			return redirect(url_for('newPostSubCategorySelect', category='jobs'))
		if 'stuff' in request.form:
			return redirect(url_for('newPostSubCategorySelect', category='stuff'))
		if 'space' in request.form:
			return redirect(url_for('newPostSubCategorySelect', category='space'))
	else:
		return render_template('category-select.html')

@app.route('/gregslist/<category>/choose/sub-category', methods=['GET', 'POST'])
@login_required
def newPostSubCategorySelect(category):
	login_session['current_url'] = request.url
	if category == 'jobs':
		job_categories = jobCategories(pagefunc='newJobForm')
		return render_template('sub-category-select.html', job_categories=job_categories)

@app.route('/gregslist/<int:category_id>/new/job/', methods=['GET', 'POST'])
@login_required
def newJobForm(category_id):
	login_session['current_url'] = request.url
	job_category = session.query(JobCategory).filter_by(id=category_id).one()
	if request.method == 'POST':
		job_post = JobPost(title=request.form['title'],
						   description=request.form['description'],
						   pay="$0.00",
						   hours="200",
						   category_id=category_id,
						   user_id=login_session['user_id'])
		flash('[success]"%s" successfully added' % request.form['title'])
		session.add(job_post)
		session.commit()
		return redirect(url_for('mainPage'))
	else:
		return render_template('create-or-edit.html',
								title="",
								description="")



def jobCategories(pagefunc='showJobCategory', mini=False, highlight=""):
	job_categories = session.query(JobCategory).order_by(asc(JobCategory.name))
	if mini:
		return render_template('job-categories-mini.html',
								job_categories=job_categories,
								pagefunc=pagefunc,
								current_category_id=highlight)
	else:
		return render_template('job-categories.html',
								job_categories=job_categories,
								pagefunc=pagefunc)

@app.context_processor
def utility_processor():
	def render_nav_bar():
		return render_template('nav-bar.html')
	def render_links_and_scripts():
		return render_template('links-and-scripts.html')
	def render_flashed_message():
		return render_template('flashed-messages.html')
	def login_provider():
		if 'provider' in login_session:
			return login_session['provider']
	return dict(render_flashed_message=render_flashed_message, 
				login_provider=login_provider,
				render_nav_bar=render_nav_bar,
				render_links_and_scripts=render_links_and_scripts)

def createUser(login_session):
	""" add user to the db """
	newUser = User(name=login_session['username'], email=login_session['email'], picture=login_session['picture'])
	session.add(newUser)
	session.commit()
	user = session.query(User).filter_by(email=login_session['email']).one()
	return user.id

def getUserInfo(user_id):
	"""retive user entry from db"""
	user = session.query(User).filter_by(id=user_id).one()
	return user

def getUserID(email):
	"""retieve user id from db using email as the input"""
	try:
		user = session.query(User).filter_by(email=email).one()
		return user.id
	except:
		return None




if __name__ == '__main__':
	app.secret_key = 'super_secret_key'
	app.debug = True
	app.run(host='0.0.0.0', port=5000)
