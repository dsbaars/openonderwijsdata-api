angular.module('myApp').controller('MatchCtrl', ['$scope', '$http', '$state', function($scope, $http, $state) {
    $scope.readMethod = "readAsText";

    $scope.onReaded = function( e, file ){

      $scope.data = e.target.result;

      $scope.json = $.parseJSON($scope.data);
      $scope.process = [];
      $.each($scope.json.deelnemers, function(i, val) {
          $http.get('http://localhost:5001/api/v1/search?geo_location=' + val.positie.lat + ',' + val.positie.lng + '&indexes=duo2&geo_distance=0.3km&reference_year=2014&doc_types=vo_branch')
          .success(function(data){
              console.log(val.naam);


              if (!data.total) {
                  $http.get('http://bag42.nl/api/v0/geocode/json?latlng=' + val.positie.lat + ',' + val.positie.lng)
                  .success(function(geodata){
                    city = _.find(geodata.results[0].address_components, function(c) {
                        if (c.types[0] == 'locality')
                            return c;
                    });

                      $http.get('http://localhost:5001/api/v1/search?q=' + val.naam + '&city=' + city.long_name + '&indexes=duo2&geo_distance=0.3km&reference_year=2014&doc_types=vo_branch')
                      .success(function(data2){
                          $scope.process.push(
                              {
                                  origin: val,
                                  match: data2.hits[0]
                              }
                          );
                      });
                  });


              } else {

              $scope.process.push(
                  {
                      origin: val,
                      match: data.hits[0]
                  }
              );
          }
              //console.log();
          });
      });
    };
}]);
