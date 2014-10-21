import rawes
import os
import exporters
import rawes

es = rawes.Elastic('http://localhost:9200')
indexes = ['duo', 'dans', 'ocw', 'schoolvo', 'onderwijsinspectie']

for i in indexes:
    try:
        testDuo = es.head(i)
    except rawes.elastic_exception.ElasticException:
        print "Index %s does not exist" % i
        es.put(i)
