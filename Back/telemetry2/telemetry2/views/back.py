import datetime
from vincenty import vincenty
import os
import uuid
import shutil
import logging
import utm
import random
from copy import deepcopy
import sys
import json
import pandas as pd 
from tempfile import NamedTemporaryFile 
import numpy as np
from math import radians, sin, cos, acos, sqrt
import statistics
from pyramid.view import view_config
from pyramid.response import Response
from pyramid.request import Request
from ..smallestenclosingcircle import *

#Algorithm from csv file
@view_config(route_name='upload', renderer='json',request_method='POST')
def init_upload(request):
    parameters = json.loads(request.POST.get('parameters')) #get parameters from post + eval() enables to get it back to dict
    technology = parameters['technology']
    species = parameters['species']
    speciesType = parameters['speciesType']
    maxSpeed = float(parameters['speed'])
    immo_time = float(parameters['immoTime'])
    deploymentDate = parameters['deploymentDate']
    objFile = request.POST.get('file') # gets csv file uploaded from Front app
    WantedData=['event-id','timestamp','location-lat','location-long'] # à demander en paramètres d'entrée
    optionalData=['elevation','hdop','info']
    rawPointsDf = DataFrameManagement(objFile,WantedData,optionalData) # function to have a df with expected data
    rawPointsDf.insert(len(rawPointsDf.columns),'status','')
    trustedPointsdf,impossiblePointsdf=prefilterData(rawPointsDf)
    # To annotate points
    impossiblePointsdf['status'] = 'impossible'
    rawPointsDf.loc[rawPointsDf.id.isin(impossiblePointsdf.id), ['status']] = impossiblePointsdf[['status']].values
    candidateDf = rawPointsDf.loc[(~rawPointsDf['id'].isin(impossiblePointsdf.id))]
    duplicatesToDelete = findDuplicates(candidateDf)
    if duplicatesToDelete is not None:
        rawPointsDf.loc[rawPointsDf.id.isin(duplicatesToDelete.id), ['status']] = duplicatesToDelete[['status']].values
    workingDf = pd.concat([candidateDf, duplicatesToDelete]).drop_duplicates(keep=False)
    # workingDf.insert(len(workingDf.columns),'status','pending')
    points_prefiltered = dfToListDict(workingDf)
    rawPointsAnnotated = dfToListDict(rawPointsDf)
    rawPointsAnnotated, eliminatedSpeed,points_filtered1, alertDate, points=Speed_algo(rawPointsAnnotated,points_prefiltered,maxSpeed,deploymentDate) # voir quoi faire avec rawPoints
    if alertDate == 1:
        return rawPointsAnnotated,points_prefiltered,dfToListDict(impossiblePointsdf), eliminatedSpeed, points_filtered1, speciesType, alertDate
    if technology == 'argos':
        Argoserror = ArgosError()
        rawPointsAnnotated,detected_immo,points_filtered2 = Immobility_algo(rawPointsAnnotated,points_filtered1,Argoserror,immo_time) # x= distance maximale entre un point et fin + à partir d'un nombre de points. + données d'activité! Attention Argos, 30km d'erreur
    if technology == 'gps':
        Gpserror = GpsError()
        rawPointsAnnotated,detected_immo,points_filtered2 = Immobility_algo(rawPointsAnnotated,points_filtered1,Gpserror,immo_time) # x= distance maximale entre un point et fin + à partir d'un nombre de points. + données d'activité! Attention Argos, 30km d'erreur
    trackInfo=calculateStats(points_filtered2)
    return rawPointsAnnotated,points_prefiltered,dfToListDict(impossiblePointsdf), eliminatedSpeed, points_filtered2, detected_immo, speciesType, alertDate, trackInfo
    

def returnGoodCSV (f):
    with NamedTemporaryFile(dir='.', delete=False, mode='w+b', suffix='.csv') as resultfile:
        filecontent : str = f.read()
        filecontent = filecontent.replace(b',',b';')
        
        resultfile.write(filecontent)
        return resultfile.name

def DataFrameManagement(objFile,WantedData,optionalData):
    goodCSVFileName = returnGoodCSV (objFile.file)

    data = pd.read_csv(goodCSVFileName,sep = ';',dtype=str)
    for option in optionalData:
        for col in data.columns:
            if option == col:
                WantedData.append(option)
    dataM=data[WantedData] # Keep only WantedData from the dataframe
    L=len(dataM.columns)
    ExpectedLabels = ['id','date','LAT','LON','elevation','HDOP','info'] # list of labels of the most complete dataset 
    dataM.columns = ExpectedLabels[:L]
    for i in ExpectedLabels[L:]:
        dataM.insert(len(dataM.columns),i,'')
    dataM['date'] = dataM['date'].str.replace(" ","T") #to have date in appropiate format
    dataM['date'] = pd.to_datetime(dataM["date"]).dt.strftime('%Y-%m-%dT%H:%M:%S') # souci dans conversion
    dataM = dataM.sort_values(by='date',ascending=True)
    dataM = dataM.replace({'':np.NAN})

    return dataM

#Aglorithm from data in textarea
@view_config(route_name='backapp', renderer='json',request_method='POST')
def init_back(request):
    parameters = json.loads(request.POST.get('parameters')) #get parameters from post + eval() enables to get it back to dict
    technology = parameters['technology']
    species = parameters['species']
    speciesType = parameters['speciesType']
    maxSpeed = float(parameters['speed'])
    immo_time = float(parameters['immoTime'])
    deploymentDate = parameters['deploymentDate']
    if "geometry" in request.POST:
        geometry = request.POST.get('geometry')
        pb=[]
        #step1 parsing data
        points_distinct,pb=parsingRequest(geometry,pb)
        # ordered by date
        rawPointsDf=orderByDate(points_distinct)
        rawPointsDf.insert(len(rawPointsDf.columns),'status','')
        #step2 prefiltre
        trustedPointsdf,impossiblePointsdf=prefilterData(rawPointsDf)
        # To annotate points
        impossiblePointsdf['status'] = 'impossible'
        rawPointsDf.loc[rawPointsDf.id.isin(impossiblePointsdf.id), ['status']] = impossiblePointsdf[['status']].values
        #step3 estimation
        if len(pb)==0:
            candidateDf = rawPointsDf.loc[(~rawPointsDf['id'].isin(impossiblePointsdf.id))]
            # to delete duplicates
            duplicatesToDelete = findDuplicates(candidateDf)
            # To annotate points
            if duplicatesToDelete is not None:
                duplicatesToDelete['status'] = 'duplicate'
                rawPointsDf.loc[rawPointsDf.id.isin(duplicatesToDelete.id), ['status']] = duplicatesToDelete[['status']].values
            workingDf = pd.concat([candidateDf, duplicatesToDelete]).drop_duplicates(keep=False)
            # points_prefiltered=annotatedResult(rawPointsDf,impossiblePointsdf,trustedPointsdf,workingDf)
            # to delete points very far from other data
            # workingDf['status']='pending' # à voir si on garde ce statut
            points_prefiltered = dfToListDict(workingDf)
            # points_prefiltered = workingDf.to_dict('Index').values()
            # points_filtered1=Distance_algo(points_prefiltered,2,10)
            # pointsDistance = Distance_algo(points_prefiltered)
            # Add speed info 
            rawPointsAnnotated = dfToListDict(rawPointsDf)
            rawPointsAnnotated, eliminatedSpeed,points_filtered =Speed_algo(rawPointsAnnotated,points_prefiltered,maxSpeed,deploymentDate) # voir quoi faire avec rawPoints
            if technology == 'argos' :
                Argoserror = ArgosError()
                rawPointsAnnotated,detected_immo,points_filtered = Immobility_algo(rawPointsAnnotated,points_filtered,Argoserror,immo_time) # x= distance maximale entre un point et fin + à partir d'un nombre de points. + données d'activité! Attention Argos, 30km d'erreur
            if technology == 'gps' :
                Gpserror = GpsError()
                rawPointsAnnotated,detected_immo,points_filtered = Immobility_algo(rawPointsAnnotated,points_filtered,Gpserror,immo_time) # x= distance maximale entre un point et fin + à partir d'un nombre de points. + données d'activité! Attention Argos, 30km d'erreur
            return rawPointsAnnotated,points_prefiltered,dfToListDict(impossiblePointsdf), eliminatedSpeed, points_filtered, detected_immo, speciesType # ,duplicates 
        else :
            return 'souci'
    else:
        return 'no params'

def dfToListDict(dataframe):
    toret = []
    dataframe = dataframe.replace({np.NAN:None})
    rows = dataframe.to_dict('Index').values()
    for row in rows:
        toret.append(row)
    return  toret


def parsingRequest(options,pb):
    points=options.split('\n')
    points_distinct=[]    
    i=0
    long=len(points)
    while i < long : 
        tempData = points[i].split(',')
        nbCol = len(tempData)
        if nbCol < 3:
            pb.append(1)
        else:
            points_distinct.append({
                'id':tempData[0] if 0 < nbCol else '' ,
                'date':tempData[1] if 1 < nbCol else '',
                'LAT':tempData[2] if 2 < nbCol else '' , 
                'LON':tempData[3] if 3 < nbCol else '' ,
                'elevation': tempData[4] if 4 < nbCol else '',
                'HDOP': tempData[5] if 5 < nbCol else '' ,
                'info': tempData[6] if 6 < nbCol else ''
            })
        i=i+1
    return points_distinct, pb
   
def orderByDate(data):
    pFrame = pd.DataFrame(data)
    pFrame['date'] = pd.to_datetime(pFrame["date"]).dt.strftime('%Y-%m-%dT%H:%M:%S')
    pFrame = pFrame.sort_values(by='date',ascending=True)
    pFrame = pFrame.replace({'':np.NAN})
    return pFrame 


def prefilterData(data):
    eleminatedPointsdf = findPointsToEliminate(data)
    trustedPointsdf = findTrustedPoints(data)
    return trustedPointsdf , eleminatedPointsdf

def findPointsToEliminate(data):
    dataToEliminate = data.loc[(~data['info'].isin(['2D','3D',np.NAN]))|((data['LAT'].isnull())|(data['LON'].isnull()))] # & (abs(float(data['LAT']>90))) & (abs(float(data['LON']>180)))]
    return dataToEliminate

def findTrustedPoints(data):
    return data.loc[(data['HDOP']=='0.7') & (data['info'].isin(['2D','3D',np.NAN]))]

def algoConfigParameters():
    parametersToEnter = {
        'MAXSPEEDRATE':'',
        'MAXALT':'',
        'MINALT':'',
     }
    return 0

def annotatedResult(rawPointsDf,eleminatedPointsdf,trustedPointsdf):
    pendingPointsDf=rawPointsDf.loc[(~rawPointsDf['id'].isin(eleminatedPointsdf.id))&(~rawPointsDf['id'].isin(trustedPointsdf.id))]
    pendingPointsDf.insert(len(pendingPointsDf.columns),'status','pending')
    
    return dfToListDict(pendingPointsDf)
    # points_prefiltered = []
    
    # rows = pendingPointsDf.to_dict('Index').values()
    # for row in rows:
    #     points_prefiltered.append(row)
    # return  points_prefiltered

# def annotatedResult(df,trustedPointsList,eleminatedPointsList):
#     points_prefiltered=[]
#     m = df.to_dict('Index').values()
#     for item in m:
#         item['status']='pending'
#         points_prefiltered.append(item)
#     for itemt in trustedPointsList:
#         points_prefiltered[itemt]['status'] = 'trust'
#     for iteme in eleminatedPointsList:
#         points_prefiltered[iteme]['status'] = 'toEliminate'

#     return  points_prefiltered

def findDuplicates(candidateDf):
    # Dataframe rassemblant tous les doublons sur la date
    allDuplicatedDf = candidateDf[candidateDf.duplicated(['date'],keep=False)]
    # Récupération des dates pour lesquelles il y a des doublons
    listDateGroup = allDuplicatedDf['date'].unique().tolist()
    duplicatedRowsToDelete = None
    # Récupération des doublons d'une date donnée
    for date in listDateGroup:
        currentDf = allDuplicatedDf.loc[allDuplicatedDf['date']==date]
        # Dénombrement des colonnes vides pour chaque doublon
        currentDf['total'] = currentDf.isnull().sum(axis=1)
        currentDfOrdered = currentDf.sort_values(by='total',ascending=True)
        # Ajout à la collection des doublons sauf pour le premier qui est donc conservé car étant plus complet 
        duplicatedRowsToDelete = pd.concat([currentDfOrdered[1:],duplicatedRowsToDelete])
    if duplicatedRowsToDelete is not None:
        duplicatedRowsToDelete = duplicatedRowsToDelete.drop(['total'] , axis=1)
    return duplicatedRowsToDelete


    Nduplicates = []
    L=len(data)
    for i in range(L-1):
        if data[i]['date']==data[i+1]['date']:
            S1=sum(value == '' for value in data[i].values())
            S2=sum(value == '' for value in data[i+1].values())
            if S1<S2:
                Nduplicates.append(data[i])
            elif S1>S2:
                Nduplicates.append(data[i+1])
            else :
                Nduplicates.append(data[i])
        else: 
            Nduplicates.append(data[i])
    if data[L-2]['date']!=data[L-1]['date']:
        Nduplicates.append(data[L-1])
    return Nduplicates

# Version qui laisse les points trop éloignés en pending
# def Distance_algo(points):
#     pointsfiltered=deepcopy(points)
#     L=len(pointsfiltered)
#     #MAX=100
#     pointsfiltered[0]['distance1'] = 0
#     for i in range (L-1):
#         slat = radians(float(pointsfiltered[i]['LAT']))
#         slon = radians(float(pointsfiltered[i]['LON']))
#         elat = radians(float(pointsfiltered[i+1]['LAT']))
#         elon = radians(float(pointsfiltered[i+1]['LON']))
#         dist = 6371.01 * acos(sin(slat)*sin(elat) + cos(slat)*cos(elat)*cos(slon - elon))
#         pointsfiltered[i+1]['distance1'] = dist
#         if pointsfiltered[i+1]['distance1']< 2:
#             pointsfiltered[i]['status']='retained'
#             pointsfiltered[i+1]['status']='retained'
#         if i<=1:
#             pointsfiltered[i]['distance2'] = 0
#         else:
#             tlat = radians(float(pointsfiltered[i-2]['LAT']))
#             tlon = radians(float(pointsfiltered[i-2]['LON']))
#             dist2 = 6371.01 * acos(sin(slat)*sin(tlat) + cos(slat)*cos(tlat)*cos(slon - tlon))
#             pointsfiltered[i]['distance2']=dist2
#             if pointsfiltered[i]['distance2'] < 2:
#                 pointsfiltered[i]['status']='retained'
#     slat = radians(float(pointsfiltered[L-1]['LAT']))
#     slon = radians(float(pointsfiltered[L-1]['LON']))
#     tlat = radians(float(pointsfiltered[L-3]['LAT']))
#     tlon = radians(float(pointsfiltered[L-3]['LON']))
#     dist2 = 6371.01 * acos(sin(slat)*sin(tlat) + cos(slat)*cos(tlat)*cos(slon - tlon))
#     pointsfiltered[L-1]['distance2']=dist2 
#     return pointsfiltered

# Version delete far points + calculated distance with Vicenty
# def Distance_algo(points,max1,max2):
#     pointsfiltered=[]
#     L=len(points)
#     #MAX=100
#     points[0]['distance1'] = 0
#     for i in range (L-1):
#         points[i+1]['distance1'] = vincenty((float(points[i]['LAT']),float(points[i]['LON'])),(float(points[i+1]['LAT']),float(points[i+1]['LON'])))
#         if points[i+1]['distance1']< max1:
#             points[i]['status']='retained'
#             points[i+1]['status']='retained'
#         if i<=1:
#             points[i]['distance2'] = 0
#         else:
#             points[i]['distance2']=vincenty((float(points[i-2]['LAT']),float(points[i-2]['LON'])),(float(points[i]['LAT']),float(points[i]['LON'])))
#             if points[i]['distance2'] < max2:
#                 points[i]['status']='retained'
#         if points[i]['status']=='retained':
#             pointsfiltered.append(points[i])
#     points[L-1]['distance2']=vincenty((float(points[L-3]['LAT']),float(points[L-3]['LON'])),(float(points[L-1]['LAT']),float(points[L-1]['LON'])))
#     if points[L-1]['distance2'] <max2:
#         points[L-1]['status']='retained'
#     if points[L-1]['status']=='retained':
#         pointsfiltered.append(points[L-1])
#     return pointsfiltered

    #Version garde far points + calculated distance with Vicenty
# def Distance_algo(points):
#     L=len(points)
#     #MAX=100
#     points[0]['distance1'] = 0
#     for i in range (L-1):
#         points[i+1]['distance1'] = vincenty((float(points[i]['LAT']),float(points[i]['LON'])),(float(points[i+1]['LAT']),float(points[i+1]['LON'])))
#         if i<=1:
#             points[i]['distance2'] = 0
#         else:
#             points[i]['distance2']=vincenty((float(points[i-2]['LAT']),float(points[i-2]['LON'])),(float(points[i]['LAT']),float(points[i]['LON'])))
#     points[L-1]['distance2']=vincenty((float(points[L-3]['LAT']),float(points[L-3]['LON'])),(float(points[L-1]['LAT']),float(points[L-1]['LON'])))
#     return points

# Algo usable if 1st point correct. If movement to the next location (from i to i+1) requires implausible speed, i+1 is marked outlier and i is tested with the next one
# until a plausible location is found
def Speed_algo(rawPointsAnnotated,points,MaxSpeed,deploymentDatestr):
    eliminatedSpeed =[]
    pointsfiltered = []
    deploymentDateobj = datetime.datetime.strptime(deploymentDatestr, '%Y-%m-%dT%H:%M')
    deploymentDateobj = deploymentDateobj.isoformat()
    L=len(points)
    start = 0
    alertDate = 0
    # Vérification que la date de déploiement soit bien avant la date de la dernière donnée 
    if points[L-1]['date'] < deploymentDateobj:
        alertDate = 1
        return rawPointsAnnotated, eliminatedSpeed, pointsfiltered, alertDate
    # Recherche de l'indice de la donnée correspondant au déploiement pour commencer le filtre de vitesse à partir de cet indice
    if points[0]['date'] < deploymentDateobj:
        for d in range (L):
            if points[d]['date'] >= deploymentDateobj:
                start = d
                break 
            else:
                eliminatedSpeed.append(points[d]) 
                for l in range (len(rawPointsAnnotated)):
                    if rawPointsAnnotated[l]['id'] == points[d]['id']: 
                        rawPointsAnnotated[l]['status']= 'before deployment'
                points[d]['distance1'] = 0
                points[d]['speed'] = 0   
    points[start]['distance1'] = 0
    points[start]['speed'] = 0
    i=start
    # Recherche des points valides sur la vitesse
    while i < L-1:
        for j in range (1,L-i):
            # Calcul de la distance
            points[i+j]['distance1'] = vincenty((float(points[i]['LAT']),float(points[i]['LON'])),(float(points[i+j]['LAT']),float(points[i+j]['LON'])))
            # Calcul de la durée
            diftimeS=datetime.datetime.strptime(points[i+j]['date'],'%Y-%m-%dT%H:%M:%S') - datetime.datetime.strptime(points[i]['date'],'%Y-%m-%dT%H:%M:%S')
            diftimeH=diftimeS.total_seconds()/3600
            # Calcul de la vitesse
            speed=points[i+j]['distance1']/float(diftimeH)
            points[i+j]['speed'] = speed
            # Comparaison à la vitesse maximale entrée en paramètre,
            # Si la vitesse est considérée aberrante on ajoute le point aux données éliminées et on l'annote dans les données brutes 
            if speed > MaxSpeed:
                eliminatedSpeed.append(points[i+j])
                for l in range (len(rawPointsAnnotated)):
                    if rawPointsAnnotated[l]['id'] == points[i+j]['id']: 
                        rawPointsAnnotated[l]['status']= 'speed outlier'
            else:
                i=i+j
                break 
    # Elimination des points dont la vitesse a été jugée aberrante
    pointsfiltered = [x for x in points if x not in eliminatedSpeed]          
    return rawPointsAnnotated, eliminatedSpeed, pointsfiltered, alertDate, points 
    # renvoi 1/la collection avec tous les points mais annotés, 2/les points éliminés par vitesse et 3/ 1-2
    


# def Speed_algo(points,max1,MaxSpeed):
#     pointsfilteredS=[]
#     speed=0
#     L=len(points)
#     points[0]['speed']=0
#     pointsfilteredS.append(points[0])
#     for i in range(1,L):
#         diftimeS=datetime.datetime.strptime(points[i]['date'],'%Y-%m-%dT%H:%M:%S') - datetime.datetime.strptime(points[i-1]['date'],'%Y-%m-%dT%H:%M:%S')
#         diftimeH=diftimeS.total_seconds()/3600
#         if 0<points[i]['distance1']<max1: #à voir pour la distance
#             speed=points[i]['distance1']/float(diftimeH)
#         else:
#             speed=points[i]['distance2']/float(diftimeH)
#         points[i]['speed']=speed
#         if points[i]['speed']<MaxSpeed: #à voir pour la valeur en fonction de l'espèce (50 voire 70 en pointe pour bouquetin)
#             pointsfilteredS.append(points[i])
#         # else:
#         #     # ajouter à la collection de points éliminiés
#         return pointsfilteredS
        
def ArgosError():
    error = 1000 # voir quoi mettre, maxerror, average error..
    return error 

def GpsError():
    error = 50 # voir quoi mettre, maxerror, average error..
    return error 

def Immobility_algo(rawPointsAnnotated,points,immo_range, immo_time):
    pointsAlive = []
    detected_immo = []
    L=len(points)
    points_for_circle = [] 
    K = len(points_for_circle)   
    r=0
    # Boucle en partant de la fin
    for record in reversed(points):
        # Conversion des coordonnées en degrés en coordonnées cartésiennes
        x,y,zone,p= utm.from_latlon(float(record['LAT']),float(record['LON']))
        # Ajout du point à la liste permettant de faire le cercle minimum
        points_for_circle.append((x,y))
        # Création du cercle minimum
        cx,cy,r = make_circle(points_for_circle)
        detected_immo.append(record)
        # Vérification du rayon du cercle : s'il est supérieur à l'erreur de localisation, 
        # alors le dernier point ajouté ne faisait pas partie de l'immobilité
        if r > immo_range : 
            del points_for_circle[-1]
            del detected_immo[-1]
            break
    K = len(points_for_circle)  
    # Calcul de la durée de la potentielle immobilité détectée
    diftimeS=datetime.datetime.strptime(points[L-1]['date'],'%Y-%m-%dT%H:%M:%S') - datetime.datetime.strptime(points[L-K]['date'],'%Y-%m-%dT%H:%M:%S')
    diftimeH=diftimeS.total_seconds()/3600
    # Comparaison de la durée calculée diftimeH à la durée minimale entrée en paramètre immo_time,
    # Si diftimeH >= immo_time, l'immobilité est validée
    if diftimeH >= immo_time:
        print('Immobility detected from',points[L-K]['date'])
        pointsAlive = [x for x in points if x not in detected_immo]
        # Annotation des points de l'immobilité dans la collection des données brutes
        for i in range (len(rawPointsAnnotated)):
            if rawPointsAnnotated[i]['date']>= points[L-K]['date']:
                rawPointsAnnotated[i]['status']='immobility'
    else:
        detected_immo = []
        pointsAlive = points
        print("Aucune immobilité n'a été détectée")
    return rawPointsAnnotated,detected_immo, pointsAlive

def calculateStats(points_filtered2):
    L = len(points_filtered2)
    trackDuration = datetime.datetime.strptime(points_filtered2[L-1]['date'],'%Y-%m-%dT%H:%M:%S') - datetime.datetime.strptime(points_filtered2[0]['date'],'%Y-%m-%dT%H:%M:%S')
    trackDuration = trackDuration.days
    # if trackDuration < 1:
    #     trackDuration = trackDuration/np.timedelta64(1,'D')
    # trackDurationH = trackDurationS.total_seconds()/3600
    overallDistance = 0
    speed = []
    for i in range(L):
        overallDistance = overallDistance + points_filtered2[i]['distance1']
        speed.append(points_filtered2[i]['speed'])
    meanSpeed = round(statistics.mean(speed),2)
    maxSpeed = round(max(speed),2)
    overallDistance = round(overallDistance)
    trackInfo = {
        'trackDuration' : trackDuration, 
        'overallDistance' : overallDistance,
        'meanSpeed' : meanSpeed,
        'maxSpeed' : maxSpeed
    }
    print(trackInfo)
    return trackInfo