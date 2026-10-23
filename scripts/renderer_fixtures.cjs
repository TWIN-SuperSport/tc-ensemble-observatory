// Test-only deterministic fixtures. Never read or extend a real forecast track.
const syntheticMonitor = {state:'active',trackingTargetId:'renderer-regression'};
const templatePoints = () => Array.from({length:21}, (_,index) => ({
  fhour:index*12,lat:15+index*.5,lon:130+index*.5,
  mslp_hpa:1000-index*.3,vmax_kt:null,
}));

function makeCoverageFixture() {
  const template=templatePoints();
  return {
    meta:{init:'2026101700',model:'GEFS',storm:'WP29',trackingTargetId:syntheticMonitor.trackingTargetId,clusterForecastHour:120},
    disclaimer:{ja:'描画テスト専用の合成データです。',en:'Synthetic renderer test data only.'},
    summary:{members:31,cleanMembers:16,terminatedMembers:10,validMembers:26,noiseMembers:5,clusterCount:2,clusteredMembers:21,
      availableMembersByLead:template.map(p=>({fhour:p.fhour,count:p.fhour<=24?26:p.fhour<=120?21:16,total:31}))},
    tracks:Array.from({length:31}, (_,index) => {
      const member=index===0?'c00':`p${String(index).padStart(2,'0')}`;
      const full=index<16,terminated=index>=16&&index<26,last=full||!terminated?240:index<21?120:24;
      return {member,cluster:full?(index<10?'C1':'C2'):terminated?'TERMINATED':'NOISE',
        scenarioCluster:terminated&&index<21?'C1':null,noiseReasons:terminated||full?[]:['tracker_jump'],
        termination:terminated?{reason:'center_lost',atForecastHour:last+12,lastValidForecastHour:last}:null,
        points:template.filter(p=>p.fhour<=last).map(p=>({...p,member,lon:p.lon+index*.1}))};
    }),
    clusters:[{id:'C1',label:'Test west',count:15,share:48.4,medianTrack:template.filter(p=>p.fhour<=120)},
      {id:'C2',label:'Test east',count:6,share:19.4,medianTrack:template.filter(p=>p.fhour<=120)}],
  };
}

function makeAllTerminatedFixture() {
  const fixture=makeCoverageFixture(),template=templatePoints();
  fixture.summary={members:31,cleanMembers:0,terminatedMembers:31,validMembers:31,noiseMembers:0,clusterCount:1,clusteredMembers:31,
    availableMembersByLead:template.map(p=>({fhour:p.fhour,count:p.fhour<=120?31:p.fhour<=168?2:0,total:31}))};
  fixture.tracks=fixture.tracks.map((track,index) => {
    const last=index<29?120:168;
    return {...track,cluster:'TERMINATED',scenarioCluster:'C1',noiseReasons:[],
      termination:{reason:index<29?'center_lost':'domain_exit',atForecastHour:last+12,lastValidForecastHour:last},
      points:template.filter(p=>p.fhour<=last).map(p=>({...p,member:track.member,lon:p.lon+index*.1}))};
  });
  fixture.clusters=[{id:'C1',label:'Synthetic common prefix',count:31,share:100,medianTrack:template.filter(p=>p.fhour<=120)}];
  return fixture;
}

module.exports={makeCoverageFixture,makeAllTerminatedFixture,syntheticMonitor};
