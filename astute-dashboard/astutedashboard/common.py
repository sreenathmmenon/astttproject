#
# Copyright 2017 NephoScale
#

#from keystoneauth1.identity import v2
#from keystoneauth1 import session
#from keystoneclient.v2_0.client import Client as KeystoneClient

#from novaclient.client import Client as NovaClient

from django.conf import settings

from horizon.exceptions import HorizonException
from horizon.utils import functions as utils
from openstack_dashboard import api
from openstack_dashboard.api import base
from openstack_dashboard.api import cinder
from openstack_dashboard.api import keystone
from openstack_dashboard.api import nova
from openstack_dashboard.api import neutron
from openstack_dashboard.usage import quotas
from openstack_dashboard.api import glance

from openstack_dashboard.local.local_settings import \
    ADMIN_AUTH_URL, \
    ADMIN_USERNAME, \
    ADMIN_PASSWORD, \
    ADMIN_TENANT, \
    OPENSTACK_API_VERSIONS

if OPENSTACK_API_VERSIONS['identity'] >= 3:
    from keystoneclient.v3 import client as ksclient
else:
    from keystoneclient.v2_0 import client as ksclient

from cinderclient import client as cclient
from neutronclient.v2_0 import client
from novaclient import client as nova_client
from glanceclient.v1 import client as glance_client
import requests as http
from openstack_dashboard.dashboards.project.images \
    import utils as image_utils
#from datetime import timedelta, date

import datetime

# Astute service base URL
ASTUTE_BASE_URL = getattr(settings, 'ASTUTE_BASE_URL', 'http://os-controller1:9080/v1/')

# Astute service request
def astute(request, endpoint, method='GET', data=None):
    auth_token = request.META.get('X_AUTH_TOKEN') or request.session['token'].id
    response = getattr(http, method.lower())(
                   ASTUTE_BASE_URL + endpoint,
                   headers={'X-Auth-Token': auth_token},
                   json=data
               )

    print '------------------------------'
    print response
    if getattr(settings, 'DEBUG'):
        print "###", "ASTUTE RESPONSE ### " * 5
        print response, response.text
        print response

    return response.json()

#Added as some of the admin functionality has to be given for M1 specific roles too
def get_admin_ksclient():
    keystone = ksclient.Client(
        username    = ADMIN_USERNAME,
        password    = ADMIN_PASSWORD,
        tenant_name = ADMIN_TENANT,
        auth_url    = ADMIN_AUTH_URL
    )    
    if OPENSTACK_API_VERSIONS['identity'] >= 3:
        keystone.tenants = keystone.projects
    return keystone

def get_cinder_client():
    cinder = cclient.Client('2', ADMIN_USERNAME, ADMIN_PASSWORD, ADMIN_TENANT, ADMIN_AUTH_URL)
    return cinder

def get_neutron_client():
    neutron = client.Client(username = ADMIN_USERNAME,
                                password = ADMIN_PASSWORD,
                                tenant_name = ADMIN_TENANT,
                                auth_url = ADMIN_AUTH_URL)
    return neutron

def get_nova_client():
    nova = nova_client.Client(2,
                              ADMIN_USERNAME,
                              ADMIN_PASSWORD,
                              ADMIN_TENANT,
                              ADMIN_AUTH_URL)
    return nova


#
# Common OpenStack API helpers
#

# @returns list projects
def get_projects(request):
    #auth = v2.Password(username=ADMIN_USERNAME, password=ADMIN_PASSWORD, \
    #    tenant_name=ADMIN_TENANT, auth_url=ADMIN_AUTH_URL)
    #sess = session.Session(auth=auth)
    #keystone = KeystoneClient(session=sess)

    #Role based check has to be added
    #Passing the admin paramter temporarily to fix issue while loading as users with provisioning, finance, support or catalogue roles
    ks = get_admin_ksclient()
    return ks.tenants.list()
    #return keystone.tenant_list(request)[0]

def get_tenants(request, paginate=False, marker=None, domain=None, 
                user=None, admin=True, filters=None):
    ksclient = get_admin_ksclient()
    page_size = utils.get_page_size(request)
    limit     = None

    if paginate:
        limit = page_size + 1

    has_more = False

    # if requesting the projects for the current user,
    # return the list from the cache
    if user == request.user.id:
        projects = request.user.authorized_tenants
    elif keystone.VERSIONS.active < 3:
        projects = ksclient.tenants.list(limit, marker)
        if paginate and len(projects) > page_size:
            projects.pop(-1)
            has_more = True
    else:
        kwargs = {
            "domain": domain,
            "user": user
        }
        if filters is not None:
            kwargs.update(filters)
        projects = ksclient.projects.list(**kwargs)
    return (projects, has_more)


# @returns project
def get_project(request, id):
    #auth = v2.Password(username=ADMIN_USERNAME, password=ADMIN_PASSWORD, \
    #    tenant_name=ADMIN_TENANT, auth_url=ADMIN_AUTH_URL)
    #Ssess = session.Session(auth=auth)
    #keystone = KeystoneClient(session=sess)
    ks = get_admin_ksclient()
    return ks.tenants.get(id)
    #return keystone.tenant_get(request, id)

# @Create a new tenant/project
def create_project(request, name, description=None, enabled=None, domain=None, **kwargs):
    ks = get_admin_ksclient()

    if keystone.VERSIONS.active < 3:
        return ks.tenants.create(name, description, True, **kwargs)
    else:
        return ks.projects.create(name, domain, description=description, enabled=enabled, **kwargs)

# @returns list users
def get_users(request):
    ks = get_admin_ksclient()
    return ks.users.list()
    #return ks.users.list()

# @Create a new user
def create_user(request, name=None, email=None, password=None, project=None,
                enabled=None, domain=None, description=None):
    ks = get_admin_ksclient()
    if keystone.VERSIONS.active < 3:
        user = ks.users.create(name, password, email, project, enabled)
        return keystone.VERSIONS.upgrade_v2_user(user)
    else:
        return ks.users.create(name, password=password, email=email,
                                  default_project=project, enabled=enabled,
                                  domain=domain, description=description)


def create_network(request, **kwargs):
    
    nclient = get_neutron_client()
    # In the case network profiles are being used, profile id is needed.
    if 'net_profile_id' in kwargs:
        kwargs['n1kv:profile'] = kwargs.pop('net_profile_id')

    if 'tenant_id' not in kwargs:
        kwargs['tenant_id'] = request.user.project_id

    body = {'network': kwargs}
    network = nclient.create_network(body=body).get('network')
    return neutron.Network(network)

def create_subnet(request, network_id, **kwargs):
    body = {'subnet': {'network_id': network_id}}

    if 'tenant_id' not in kwargs:
        kwargs['tenant_id'] = request.user.project_id
    nclient = get_neutron_client()
    body['subnet'].update(kwargs)
    subnet = nclient.create_subnet(body=body).get('subnet')
    return neutron.Subnet(subnet)

def list_network(request, **params):
    nclient = get_neutron_client()
    networks = nclient.list_networks(**params).get('networks')
    
    # Get subnet list to expand subnet info in network list.
    subnets = list_subnet(request)
    subnet_dict = dict([(s['id'], s) for s in subnets])
    
    # Expand subnet list from subnet_id to values.
    for n in networks:
        # Due to potential timing issues, we can't assume the subnet_dict data
        # is in sync with the network data.
        n['subnets'] = [subnet_dict[s] for s in n.get('subnets', []) if
                        s in subnet_dict]
    return [neutron.Network(n) for n in networks]

def list_subnet(request, **params):
    nclient = get_neutron_client()
    subnets = nclient.list_subnets(**params).get('subnets')
    return [neutron.Subnet(s) for s in subnets]

def create_router(request, **kwargs):
    body = {'router': {}}
    if 'tenant_id' not in kwargs:
        kwargs['tenant_id'] = request.user.project_id
    body['router'].update(kwargs)
    nclient = get_neutron_client()
    router = nclient.create_router(body=body).get('router')
    return neutron.Router(router)

def add_interface_to_router(request, router_id, subnet_id=None, port_id=None):
    body = {}
    if subnet_id:
        body['subnet_id'] = subnet_id
    if port_id:
        body['port_id'] = port_id
    nclient = get_neutron_client()
    return nclient.add_interface_router(router_id, body)


# @returns list of defined flavors
def get_flavors(request):
    #nova = NovaClient('2', ADMIN_USERNAME, ADMIN_PASSWORD, ADMIN_TENANT, ADMIN_AUTH_URL)
    return nova.flavor_list(request)


#
# Billing Types helper routines
#

# returns billing types list
def get_billing_types(request):
    return astute(request, 'billing/type/')

# returns billing type data
def get_billing_type(request, id):
    return astute(request, 'billing/type/' + str(id))

# create billing type
def create_billing_type(request, data):
    return astute(request, 'billing/type/', 'POST', data)

# modify billing type
def modify_billing_type(request, id, data):
    return astute(request, 'billing/type/' + str(id), 'PUT', data)

# delete billing type
def delete_billing_type(request, id):
    return astute(request, 'billing/type/' + str(id), 'DELETE')

#
# Service Tyles helper routines
#

# returns service types list
def get_service_types(request):
    return astute(request, 'service_type/')

# returns service type data
def get_service_type(request, id):
    return astute(request, 'service_type/' + str(id))

# create service type
def create_service_type(request, data):
    return astute(request, 'service_type/', 'POST', data)

# modify service type
def modify_service_type(request, id, data):
    return astute(request, 'service_type/' + str(id), 'PUT', data)

# delete service type
def delete_service_type(request, id):
    return astute(request, 'service_type/' + str(id), 'DELETE')

#
# Discount helper routines
#

# returns discount types list
def get_discount_types(request):
    return astute(request, 'discount/type/')

# returns discount type data
def get_discount_type(request, id):
    return astute(request, 'discount/type/' + str(id))

#
# Discounts helper routines
#

# @returns discounts list
def get_discounts(request):
    data = astute(request, 'discount/')
    # adjust data
    for item in data:
        item['create_time'] = item.get('create_time') and item['create_time'].split(' ')[0]
        item['expiration_date'] = item.get('expiration_date') and item['expiration_date'].split(' ')[0]
    return data

# @returns discount data
def get_discount(request, id):
    data = astute(request, 'discount/' + str(id))
    # adjust data
    data['create_time'] = data.get('create_time') and data['create_time'].split(' ')[0]
    data['expiration_date'] = data.get('expiration_date') and data['expiration_date'].split(' ')[0]
    return data

# create discount
def create_discount(request, data):
    return astute(request, 'discount/', 'POST', data)

# modify discount
def modify_discount(request, id, data):
    return astute(request, 'discount/' + str(id), 'PUT', data)

# delete discount
def delete_discount(request, id):
    return astute(request, 'discount/' + str(id), 'DELETE')


#
# Plans helper routines
#

# @returns plans list
def get_plans(request, verbose = False):
    plans = astute(request, 'plan/')
    if verbose:
        billing_types = dict([(bt['id'], bt) for bt in get_billing_types(request)])
        billing_types[0] = {'name': None}
        flavors = dict([(fl.id, fl.name) for fl in get_flavors(request)])
        service_types = dict([(st['id'], st) for st in get_service_types(request)])
        for plan in plans:
            billing_type_id = plan.get('billing_type') or 0
            plan['billing_type'] = billing_types[billing_type_id]['name']
            flavor_id = plan.get('ref_id', None)
            if flavor_id and str(flavor_id) != '0':
                plan['ref_id'] = flavors.get(flavor_id, None) or '!ERR: %s' % plan['ref_id']
            else:
                plan['ref_id'] = None
            plan['service_type'] = service_types[plan['service_type']]['name']
    return plans

# @returns plan data
def get_plan(request, id, verbose = False):
    plan = astute(request, 'plan/' + str(id))
    if verbose:
        billing_types = dict([(bt['id'], bt) for bt in get_billing_types(request)])
        billing_types[0] = {'name': None}
        flavors = dict([(fl.id, fl.name) for fl in get_flavors(request)])
        service_types = dict([(st['id'], st) for st in get_service_types(request)])
        plan['billing_type'] = billing_types[plan.get('billing_type') or 0]['name']
        flavor_id = plan.get('ref_id', None)
        if flavor_id:
            plan['ref_id'] = flavors.get(flavor_id, None) or '#UNKNOWN: %s' % plan['ref_id']
        plan['service_type'] = service_types[plan['service_type']]['name']
    return plan

# create plan
def create_plan(request, data):
    return astute(request, 'plan/', 'POST', data)

# modify plan
def modify_plan(request, id, data):
    return astute(request, 'plan/' + str(id), 'PUT', data)

# delete plan
def delete_plan(request, id):
    return astute(request, 'plan/' + str(id), 'DELETE')


#
# Billing Type Mapping routines
#

def get_billing_type_mappings(request, verbose=False):
    data = astute(request, 'billing/mapping/')
    if verbose:
        projects = dict([(p.id, p.name) for p in get_projects(request)])
        billing_types = dict([(bt['id'], bt) for bt in get_billing_types(request)])
        billing_types[0] = {'name': None}
        for item in data:
            item['user_id'] = item['user']
            item['user'] = projects.get(item['user']) or '!ERR: ' + item['user']
            item['billing_type_code'] = billing_types[item['billing_type']]['code']
            item['billing_type'] = billing_types[item['billing_type']]['name']
            extra_fields = item['extra_fields']
            item['customer_name'] = extra_fields['customer_name'] if extra_fields.has_key('customer_name') else '-'
            item['service_id'] = extra_fields['service_id'] if extra_fields.has_key('service_id') else '-'
        # add discounts
        for item in data:
            item['discount_mapping'] = get_type_mapping_discount_mapping(request, item['user_id'], verbose)
    return data

def get_billing_type_mapping(request, id, verbose=False):
    data = astute(request, 'billing/mapping/' + str(id))
    data['project'] = get_project(request, data['user'])
    data['discount_mapping'] = get_type_mapping_discount_mapping(request, id, verbose)
    return data

# create billing type mapping
def create_billing_type_mapping(request, data):
    return astute(request, 'billing/mapping/', 'POST', data)

# modify billing type mapping
def modify_billing_type_mapping(request, id, data):
    return astute(request, 'billing/mapping/' + str(id), 'PUT', data)

# delete billing type mapping
def delete_billing_type_mapping(request, id):
    return astute(request, 'billing/mapping/' + str(id), 'DELETE')

# get project type mapping (none if not mapped)
# @param id {str} - openstack project ID
def get_project_type_mapping(request, id):
    # get billing types
    bill_types = dict([(bt['id'], bt) for bt in get_billing_types(request)])
    for type_map in get_billing_type_mappings(request):
        if type_map['user'] == str(id):
            type_map['billing_type'] = bill_types[type_map['billing_type']]
            return type_map
    return None


#
# Billing Plan Mapping routines
#

def get_billing_plan_mappings(request, project_id=None, verbose=False):
    data = astute(request, 'plan/mapping/' + ('user?id=' + str(project_id) if project_id else ''))
    if verbose:

        #Fetch the details of all instances
        server_list = get_instances(request)

        projects = dict([(p.id, p.name) for p in get_projects(request)])
        plans = {}
        srv_types = {}
        for plan in get_plans(request):
            plans[plan['id']] = plan['name']
            srv_types[plan['id']] = plan['service_type']
        for item in data:
            if str(item['ref_id']):

                #Fetch the dict containing id and name of each vm
                #vm_info = (i for i in server_list if i["id"] == item['ref_id']).next()
                vm_info = next((i for i in server_list if i["id"] == item['ref_id']), None)
                
                #Fetch the vm name and use it for displaying in frontend if it's present
                if vm_info:
                    item['vm_name'] = vm_info.get('name')
                else:
                    item['vm_name'] = None
            else:
                item['vm_name'] = None
            item['user'] = projects.get(item['user'], None) or '!ERR: %s' % item['user']
            item['plan'] = plans.get(item['plan_id'], None) or '!ERR: %s' % item['plan_id']
            item['service_type'] = srv_types.get(item['plan_id'], None) or '!ERR: %s' % item['plan_id']
            item['created_on'] = item.get('created_on').split(' ')[0]
    return data

def get_billing_plan_mapping(request, id, verbose=False):
    return astute(request, 'plan/mapping/' + str(id))

# create billing plan mapping
def create_billing_plan_mapping(request, data):
    return astute(request, 'plan/mapping', 'POST', data)

# modify billing plan mapping
def modify_billing_plan_mapping(request, id, data):
    return astute(request, 'plan/mapping/' + str(id), 'PUT', data)

# delete billing plan mapping
def delete_billing_plan_mapping(request, id):
    return astute(request, 'plan/mapping/' + str(id), 'DELETE')


#
# Invoices helper routines
#

def get_invoices(request, account=None, period_from=None, period_to=None, verbose=False):

    url = 'invoice' + ('?' if account or period_from or period_to else '')

    params = []

    if account:
        params.append('user=' + str(account))
    if period_from:
        params.append('inv_from=' + str(period_from))
    if period_to:
        params.append('inv_to=' + str(period_to))
    if len(params) > 0:
        url += "&".join(params)

    data = astute(request, url)
    if verbose:
        projects = dict([(p.id, p) for p in get_projects(request)])
        accounts = dict([(a['user'], a) for a in get_billing_type_mappings(request)])

        for item in data:
            project = projects.get(item['user'], None)
            account = accounts.get(item['user'], None)
            item['user'] = project and project.name or '!ERR'
            item['service_id'] = account and account['extra_fields'] and account['extra_fields']['service_id'] or '!ERR: %s' % item['user']
            #item['inv_date'] = item['inv_date'].split(' ')[0]
            #item['inv_from'] = item['inv_from'].split(' ')[0]
            #item['inv_to'] = item['inv_to'].split(' ')[0]
            item['last_updated'] = item['last_updated'].split(' ')[0]
    return data

def get_invoice(request, id, verbose=False):
    data = astute(request, 'invoice/' + str(id))
    if verbose:
        projects = dict([(p.id, p.name) for p in get_projects(request)])
        plans = dict([(p['id'], p['name']) for p in get_plans(request)])
        data['user'] = projects.get(data['user'], None) or '!ERR: %s' % data['user']
        data['inv_date'] = data['inv_date'].split(' ')[0]
        data['inv_from'] = data['inv_from'].split(' ')[0]
        data['inv_to'] = data['inv_to'].split(' ')[0]
        data['last_updated'] = data['last_updated'].split(' ')[0]
        for item in data['items']:
            plan_details = get_plan(request, item['plan_id'])
            
            # Set plan name
            if plan_details['service_code'] == 'IAAS':
                item['plan'] = item['description']
            else:
                item['plan'] = plans.get(item['plan_id'], None) or '!ERR: %s' % item['plan_id']
    return data

def modify_invoice(request, id, data):
    return astute(request, 'invoice/' + str(id), 'PUT', data)

#
# Discount mappings helper routines
#

def get_discount_mappings(request):
    return astute(request, 'discount/mapping/')

def get_discount_mapping(request, id):
    return astute(request, 'discount/mapping/' + str(id))

# create discount mapping
def create_discount_mapping(request, data):
    return astute(request, 'discount/mapping/', 'POST', data)

# modify discount mapping
def modify_discount_mapping(request, id, data):
    return astute(request, 'discount/mapping/' + str(id), 'PUT', data)

# delete discount mapping
def delete_discount_mapping(request, id):
    return astute(request, 'discount/mapping/' + str(id), 'DELETE')

# discount serch helpers
def get_user_discount(request, user_id):
    return astute(request, 'discount/mapping/?map_object=user&user=' + str(user_id))[0]

# discount serch helpers
# FIXME(div): requires API side implementation (see get_user_discount)
def get_plan_discount(request, plan_id):
    discount = None
    for disc in get_discounts(request):
        if disc['map_object'] == 'user_plan' and disc['ref_id'] == plan_id:
            discount = disc
            break
    return discount


def get_type_mapping_discount_mapping(request, ref_id, verbose=False):
    ref_id = str(ref_id)
    for disc_map in get_discount_mappings(request):
        if disc_map['map_object'] == 'user' and str(disc_map['ref_id']) == ref_id :
            if verbose:
                disc_map['discount'] = get_discount(request, disc_map['discount_id'])
            return disc_map
    return None

def get_plan_mapping_discount_mapping(request, plan_map_id, verbose=False):
    disc_maps = get_discount_mappings(request)
    plan_map_id = str(plan_map_id)
    for disc_map in get_discount_mappings(request):
        if disc_map['map_object'] == 'user_plan' and str(disc_map['ref_id']) == plan_map_id:
            if verbose:
                disc_map['discount'] = get_discount(request, disc_map['discount_id'])
            return disc_map
    return None


def get_project_plan_mappings(request, project_id, unassociated=False):
    return astute(request, 'plan/mapping/' + \
                           ('unassociated_plans' if unassociated else 'user') + \
                           '?id=' + str(project_id))

def associate_plan_mapping_with_instance(request, plan_id, instance_id):
    data = {
        'user_plan_id': int(plan_id),
        'ref_id': str(instance_id)
    }
    return astute(request, 'plan/mapping/associate', 'POST', data)

def disassociate_plan_mapping_with_instance(request, plan_id):
    data = {
        'user_plan_id': int(plan_id)
    }
    return astute(request, 'plan/mapping/dissociate', 'POST', data)


# TODO (div): fill following 'constants' from DB values
PAYG_BILLING_TYPE_ID = 1
RAB_BILLING_TYPE_ID  = 2

# TODO (div): fill following 'constants' from DB values
PERCENTS_DISCOUNT_TYPE_ID = 1
OVERRIDE_DISCOUNT_TYPE_ID = 2


def get_project_volume_type_quotas(request, project_id):
    result = {}
    #client = cinder.cinderclient(request)
    #quotas = client.quotas.get(project_id)
    client = get_cinder_client()
    #quotas = client.quotas.get(project_id)
    quotas  = client.quotas.get(project_id)
    for volume_type in client.volume_types.list():
        result[volume_type.name] = getattr(quotas, 'gigabytes_' + volume_type.name, -1)
    return result

def modify_project_volume_type_quotas(request, project_id, quotas):
    #client = cinder.cinderclient(request)
    client = get_cinder_client()
    #return client.quotas.update(project_id, **quotas)
    return client.quotas.update(project_id, **quotas)

#Features corresponding to Customer Interface
def get_user_sub_plans(request, verbose=False):
    """Get all the plans subscribed by the user"""

    user_id = request.user.tenant_id
    data = astute(request, 'plan/mapping/user?id=' + str(user_id))
    for item in data:
                plan_id = item['plan_id']
                plan_details = get_plan(request, plan_id)
                plan_name = plan_details.get('name')
                service_name = plan_details.get('service_name')
                description = plan_details.get('description')
                item['name'] = plan_name
                item['service_name'] = service_name
                item['description'] = description
    return data

def get_avbl_user_plans(request, verbose=False):
    """Get all the plans under the current billing type"""

    user_id = request.user.tenant_id
    plan_list = get_plans(request)
    #user_sub_plan_list = get_user_sub_plans(req)
    user_billing_details = get_user_billing_type(request)

    if user_billing_details:
        #Billing type code should already be present for a user
        billing_type_code = user_billing_details[0]['type_code']

        #Display only additional services for rab billing type
        if billing_type_code == 'rab':
            #print "Entering rab section"
            rab_billing_typeId = user_billing_details[0]['billing_type']
            data = filter(lambda x: (x['billing_type'] == None and x['service_name'] != 'SetupFee'), plan_list)

        if billing_type_code == 'payg':
            #print "Entering payg section"
            payg_billing_typeId = user_billing_details[0]['billing_type']
            data = filter(lambda x: ((x['billing_type'] == payg_billing_typeId or x['billing_type'] == None) and (x['service_name'] != 'SetupFee')), plan_list)

        for item in data:
            if billing_type_code == 'payg':
                item['billing_type']  = 'Usage Based Billing'
            elif billing_type_code =='rab':
                item['billing_type']  = 'Resource Allocation Based Billing'
            else:
                item['billing_type']  = 'NA'
    else:
        data = []
    """
    for item in data:
        billing_type_id = item['billing_type']
    """
    # filteredList = filter(lambda x: (x['billing_type'] == 1 and x['name'] not in keyVal2List), plan_list)
    return data

def get_user_invoices(request, verbose=True):
    """Get all the invoices correspodning to the user"""

    user_id = request.user.tenant_id
    data = astute(request, 'invoice?user=' + str(user_id))

    #Rounding to 2 decimal points
    for item in data:
        item['total_amt']   = format(round(item['total_amt'], 2), '.2f')
        item['balance_amt'] = format(round(item['balance_amt'], 2), '.2f')
        item['amt_paid']    = format(round(item['amt_paid'], 2), '.2f')
    return data

def get_user_billing_type(request, verbose=False):
    """Get the billing type details corresponding to the user"""

    user_id = request.user.tenant_id
    data = astute(request, 'billing/mapping/user?id=' + str(user_id))
    return data


# create user letter
def create_user_letter(request, data):
    return astute(request, 'billing/letter/', 'POST', data)

# get user letter
def get_user_letter(request, id):
    return astute(request, 'billing/letter/user?id=' + str(id))

#Fetch the instance details
def get_instance(request, instance_id):
    nvclient = get_nova_client()
    
    #Initializing the values
    vm_name = ''
    try:

        #Fetching the instance details
        instance_details = nova.Server(nvclient.servers.get(instance_id), request)
        if instance_details:
            vm_name = instance_details.name
    except Exception as e:
        print '-------------'
        print e
        print '-------------'
        #Case when VM is in deleted state
        return 'VM Deleted'
    return vm_name


def get_instances(request):
    nvclient = get_nova_client()
    search_opts = {}
    server_list = []
    search_opts['all_tenants'] = True
    for s in nvclient.servers.list(False, search_opts):
        inst_name =  str(nova.Server(s, request).name)
        inst_id   = str(nova.Server(s, request).id)
        inst_dtls = {"id":inst_id, "name":inst_name}
        server_list.append(inst_dtls)
    return server_list

####Image Count Section
def get_image_count(request):
    '''Fetch the sql image usage count'''

    '''
    image_data = {
        '2018-04-08':{
            'f6c3db4e-9cbd-4bd5-9e9a-d902e90f7f3e': 3,
            'f6c3db4e-9cbd-4bd5-9e9a-d902e90f7f34': 1
        },
        '2018-04-09': {
            'f6c3db4e-9cbd-4bd5-9e9a-d902e90f7f3e': 2,
            'f6c3db4e-9cbd-4bd5-9e9a-d902e90f7f34': 0
        }
    }
    '''
    image_data = {  
       u'2018-04-09':{  
          u'73d9a2b6-27b3-49bf-aa89-8dc126784c44':4.0,
          u'3f7384d7-de79-4813-b766-fef00a2e68dc':1.0,
          u'4b8d8242-ed69-471a-aad3-ab63ad9e7217':1.0,
          u'6494aa7b-c4b9-43db-8bff-90a6e1d3a714':1.0,
          u'd0c639b1-1644-455c-b0e9-350a788fa531':3.0,
          u'f21da7d6-e27b-418f-8cc6-e008bc255e0c':2.0,
          u'aa3d6484-c7ee-4d06-85ce-5708cb7308c5':1.0,
          u'3bb8af1f-ab8d-4c4d-93f2-dff7ca3917ab':25.0,
          u'25c77eda-56aa-4f5e-b049-bceed49c68a8':1.0,
          u'8dd064e2-2bfc-4841-bf3a-2b4fa4e7d11e':4.0,
          u'a1de2463-4ecc-488e-926d-4c9cd8cd7f2e':1.0,
          u'5147672e-9368-4e2f-b2cd-83de899f3978':1.0
       },
       u'2018-04-08':{  
          u'73d9a2b6-27b3-49bf-aa89-8dc126784c44':4.0,
          u'3f7384d7-de79-4813-b766-fef00a2e68dc':1.0,
          u'4b8d8242-ed69-471a-aad3-ab63ad9e7217':1.0,
          u'd0c639b1-1644-455c-b0e9-350a788fa531':3.0,
          u'f21da7d6-e27b-418f-8cc6-e008bc255e0c':2.0,
          u'aa3d6484-c7ee-4d06-85ce-5708cb7308c5':2.0,
          u'3bb8af1f-ab8d-4c4d-93f2-dff7ca3917ab':25.0,
          u'25c77eda-56aa-4f5e-b049-bceed49c68a8':1.0,
          u'8dd064e2-2bfc-4841-bf3a-2b4fa4e7d11e':4.0,
          u'a1de2463-4ecc-488e-926d-4c9cd8cd7f2e':1.0,
          u'5147672e-9368-4e2f-b2cd-83de899f3978':1.0,
          u'6494aa7b-c4b9-43db-8bff-90a6e1d3a714':1.0
       },
       u'2018-04-10':{  
          u'3f7384d7-de79-4813-b766-fef00a2e68dc':1.0,
          u'73d9a2b6-27b3-49bf-aa89-8dc126784c44':4.0,
          u'4b8d8242-ed69-471a-aad3-ab63ad9e7217':1.0,
          u'6494aa7b-c4b9-43db-8bff-90a6e1d3a714':1.0,
          u'd0c639b1-1644-455c-b0e9-350a788fa531':3.0, 
          u'f21da7d6-e27b-418f-8cc6-e008bc255e0c':2.0,
          u'3bb8af1f-ab8d-4c4d-93f2-dff7ca3917ab':25.0,
          u'25c77eda-56aa-4f5e-b049-bceed49c68a8':1.0,
          u'8dd064e2-2bfc-4841-bf3a-2b4fa4e7d11e':4.0,
          u'a1de2463-4ecc-488e-926d-4c9cd8cd7f2e':1.0,
          u'5147672e-9368-4e2f-b2cd-83de899f3978':1.0
       }
    }

    print type(image_data)
    return image_data

def get_glance_client():
    '''Fetch the sql image usage count'''
    glance_endpoint = get_admin_ksclient().service_catalog.url_for(service_type='image')
    glance = glance_client.Client(version=1,
                                  endpoint    = glance_endpoint,
                                  username    = ADMIN_USERNAME,
                                  password    = ADMIN_PASSWORD,
                                  tenant_name = ADMIN_TENANT,
                                  auth_url    = ADMIN_AUTH_URL
                            )
    return glance

def get_image_list(request):
    #gc = get_glance_client()
    #image_list = gc.images.list()
    #return image_list
    #images = image_utils.get_available_images(request, project_id=None, images_cache=None)
    #image_dict = dict([(image.id, image.name) for image in images])
    windows_image_list = {
        '140d8dd4-7c53-415a-aacd-b7d0bea60624': 'Windows Server 2012 R2 Standard', 
        '1d691c57-a25c-45ec-a34d-56dd45539952': 'MSSQL 2016 Server 2016 SP1', 
        '2bd51595-08a6-41ad-be4a-413897e6897c': 'Win2K12 R2 + MS SQL 2012 Std',
        '2d03f75a-ec41-4744-9956-47b810ede8a6': 'Windows Server 2012 R2 Standard',
        '3bb8af1f-ab8d-4c4d-93f2-dff7ca3917ab': 'Windows Server 2016 SP1 Standard',
        '495ec5e8-5365-43ff-b347-da5857d535b9': 'Windows Server 2012 R2 Standard',
        '4dd55847-269e-49eb-bc10-f5cf42d0983e': 'Windows Server 2012 R2 Standard',
        '675fbd42-c759-4690-b146-3715ec5359d8': 'Windows Server 2012 R2 Standard', 
        '8b8e635e-17f4-4b8a-ab80-f28dea194d72': 'Windows Server 2008 R2 Standard',
        '8dd064e2-2bfc-4841-bf3a-2b4fa4e7d11e': 'MSSQL 2016 Server 2016 SP1',
        '96323b7c-c2d2-49aa-beab-5d5c71e74c86': 'Windows Server 2008 R2 Standard',
        'abda6307-821e-4c81-9f62-de4bb262c24f': 'Windows Server 2008 R2 Standard',
        'adf094bb-ea4e-4adb-9c43-bd0b6ea3936e': 'Windows Server 2012 R2 Standard',
        'bee6e3df-adc9-4635-a23c-61afb57b2dda': 'Windows Server 2012 R2 Standard', 
        'd0c639b1-1644-455c-b0e9-350a788fa531': 'Windows Server 2012 R2 Standard',
        'd3fdcaa6-334b-44f4-af5d-cfa34edfd4e3': 'MSSQL 2012 Server 2008 R2',
        'd7bb2afc-c6f7-4c66-8221-d6ff675a101c': 'Windows Server 2012 R2 Standard',
        'd903981b-2dcc-4b01-aa98-272c2ebdd637': 'Windows Server 2012 R2 Standard',
        'e51eab57-b96a-4d72-a728-87ede6d05cd6': 'Windows Server 2012 R2 Standard',
        'f21da7d6-e27b-418f-8cc6-e008bc255e0c': 'MSSQL 2012 Server 2012 R2',
        'f4d79947-c76a-47c7-9b75-044e7e898192': 'Windows Server 2012 R2 Standard',
        'f753bf7c-a081-4d59-909d-32b74b695ffb': 'Windows Server 2008 R2 Standard',
    }
    return windows_image_list

def get_image_name(request, image_id):
    image_list = get_image_list(request)
    if image_id in image_list:
        image_name = image_list[image_id]
    else:
        image_name = 'ERROR!!' + str(image_id)
    return image_name


def get_image_usage_report(request, period_from=None, period_to=None, verbose=False):

    ##Setting the url
    #url = 'usage/imageCount/' + ('?' if period_from or period_to else '')
    #params = []

    #For setting default conditions
      
    today      = datetime.datetime.now()
    print today
    week_ago   = today - datetime.timedelta(days=6)
    print week_ago
    today      = today.strftime('%Y-%m-%d') 
    print today
    week_ago   = week_ago.strftime('%Y-%m-%d')
    print week_ago

    #Default condition during initial loading of the report
    if not period_from:
        period_from = str(week_ago)
    if not period_to:
        period_to = str(today)

    #Setting the url
    url = 'usage/imageCount/' + ('?' if period_from or period_to else '')
    params = []
 
    #Conditions when date range is specified
    if period_from:
        params.append('start=' + str(period_from))
    if period_to:
        params.append('end=' + str(period_to))
    if len(params) > 0:
        url += "&".join(params)

    data = astute(request, url)

    report_date_list = []

    #Fetching the image list
    original_image_list = get_image_list(request)

    #Initializing
    usage_data  = {}
    usage_data1 = {}
    image_data  = data

    #start = datetime.datetime.strptime(period_from, "%Y-%m-%d")
    #end = datetime.datetime.strptime(period_to, "%Y-%m-%d")
    #date_array = (start + datetime.timedelta(days=x) for x in range(0, (end-start).days + 1 ))

    #for date_object in date_array:
    #    date_val = date_object.strftime("%Y-%m-%d")
    #    report_date_list.append(date_val)

    #Remove key-value pair if the key isn't present in the list of Windows/SQL images fetched above
    for date in list(image_data.keys()):
        for user in list(image_data[date].keys()):
            if user not in original_image_list:
                del image_data[date][user]
    
    print '%%%%%%%'
    print type(period_from)
    print type(period_to)
    print image_data

    #Loop through the data and convert into different dict format
    if image_data:
      for date in image_data:
        for image in image_data[date]:
            
            start = datetime.datetime.strptime(period_from, "%Y-%m-%d")
            end = datetime.datetime.strptime(period_to, "%Y-%m-%d")
            date_array = (start + datetime.timedelta(days=x) for x in range(0, (end-start).days + 1 ))
            report_date_list = []
             
            #Fetch the image name
            image_name = get_image_name(request, image)

            if image not in usage_data:
                usage_data[image] = {}
                usage_data1[image_name] = {}

            usage_data[image][date] = image_data[date][image]
            usage_data1[image_name][date] = int(image_data[date][image])

            for date_object in date_array:
                date_val = date_object.strftime("%Y-%m-%d")
                report_date_list.append(date_val)
                
                #Setting default value
                if date_val not in usage_data1[image_name]:
                    usage_data1[image_name][date_val] = '0'
   
      #Return the usage data as well as the list of dates in the given date range
      #return user_data1, report_date_list
    else:
        usage_data1 = {}
        report_date_list = []
        #return user_data1, report_date_list
    return usage_data1, report_date_list
